import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ssl
import json
import uuid
import asyncio
from typing import Callable, Optional

import paho.mqtt.client as mqtt

from printer_manager import PrinterManager
from astrbot.api import logger


def _mqtt_host(region: str) -> str:
    return "us.mqtt.bambulab.com" if region == "global" else "cn.mqtt.bambulab.com"


class BambuMQTTClient:
    def __init__(
        self,
        region: str,
        username: str,
        token: str,
        printer_manager: PrinterManager,
    ):
        self._region = region
        self._host = _mqtt_host(region)
        self._username = username
        self._token = token
        self._manager = printer_manager
        self._serials: set[str] = set()
        self._connected = False
        self._running = False
        self._offline_timer: dict[str, asyncio.Task] = {}
        self._on_offline: Optional[Callable] = None
        self._on_recovery: Optional[Callable] = None
        self._last_error: str = ""
        self._client: Optional[mqtt.Client] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None
        self._consumer_task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        return self._last_error

    def set_serials(self, serials: list[str]):
        self._serials = set(serials)

    def update_token(self, token: str):
        self._token = token

    def set_offline_callback(self, callback: Callable):
        self._on_offline = callback

    def set_recovery_callback(self, callback: Callable):
        self._on_recovery = callback

    def request_pushall(self, serial: str):
        if self._connected and self._client:
            self._client.publish(
                f"device/{serial}/request",
                json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}})
            )
            return True
        return False

    async def start(self):
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()

        client_id = f"astrbot-bambu-{uuid.uuid4().hex[:12]}"
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self._client.username_pw_set(self._username, self._token)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self._client.tls_set_context(ctx)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.enable_logger(logger)

        self._last_error = "连接中..."
        logger.info(f"MQTT 连接中: {self._host}:8883 (user={self._username})")

        self._consumer_task = asyncio.create_task(self._consume())
        self._client.connect_async(self._host, 8883, keepalive=5)
        self._client.loop_start()
        asyncio.create_task(self._connect_timeout())

    async def stop(self):
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            self._consumer_task = None
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False

    async def _connect_timeout(self):
        await asyncio.sleep(15)
        if not self._connected and self._running:
            self._last_error = "连接超时(15s)"

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        rc_value = reason_code.value if hasattr(reason_code, 'value') else reason_code
        if rc_value == 0:
            self._connected = True
            self._last_error = ""
            logger.info(f"MQTT 已连接: {self._host}")
            for serial in self._serials:
                topic = f"device/{serial}/report"
                client.subscribe(topic)
                logger.info(f"  已订阅 {topic}，已发送 PUSH_ALL + GET_VERSION")
                self.request_pushall(serial)
                client.publish(f"device/{serial}/request", json.dumps({"info": {"sequence_id": "0", "command": "get_version"}}))
                self._cancel_offline_timer(serial)
        else:
            self._last_error = f"RC={rc_value}"
            logger.warning(f"MQTT 连接失败: rc={rc_value}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._connected = False
        rc_value = reason_code.value if hasattr(reason_code, 'value') else reason_code
        self._last_error = f"断开(RC={rc_value})"
        logger.info(f"MQTT 已断开: rc={rc_value}")

    def _on_message(self, client, userdata, message):
        if self._loop and self._queue:
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait, (message.topic, message.payload)
            )
            logger.debug(f"[MQTT] enqueued: topic={message.topic} size={len(message.payload)}")

    async def _consume(self):
        while self._running:
            try:
                topic, payload = await self._queue.get()
                await self._handle_message(topic, payload)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _handle_message(self, topic: str, payload: bytes):
        serial = ""
        parts = topic.split("/")
        if len(parts) >= 2:
            serial = parts[1]

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return

        if not serial:
            return

        if "event" in data:
            event = data.get("event", {})
            event_type = event.get("event", "")
            if event_type == "client.disconnected":
                self._start_offline_timer(serial)
            elif event_type == "client.connected":
                self._cancel_offline_timer(serial)
                self._manager.mark_online(serial)
            return

        if "print" in data:
            p = data["print"]
            msg_type = p.get("msg", 0)
            if msg_type == 0:
                logger.info(f"[MQTT] serial={serial} print msg=0 state={p.get('gcode_state','?')} mc={p.get('mc_percent',0)}%")
            else:
                logger.debug(f"[MQTT] serial={serial} print msg={msg_type} size={len(payload)}")
            self._manager.update_from_pushall(serial, data["print"])
        elif "info" in data and data["info"].get("command") == "get_version":
            self._manager.update_firmware_info(serial, data["info"])

    def _start_offline_timer(self, serial: str):
        self._cancel_offline_timer(serial)
        self._offline_timer[serial] = asyncio.create_task(self._offline_task(serial))

    def _cancel_offline_timer(self, serial: str):
        task = self._offline_timer.pop(serial, None)
        if task:
            task.cancel()

    async def _offline_task(self, serial: str):
        await asyncio.sleep(60)
        self._manager.mark_offline(serial)
        if self._on_offline:
            self._on_offline(serial)
