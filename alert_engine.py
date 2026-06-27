import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import re
import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, Any

from printer_manager import (
    PrinterState, STATE_IDLE, STATE_RUNNING, STATE_PAUSE,
    STATE_PREPARE, STATE_FINISH, STATE_FAILED,
)

logger = logging.getLogger(__name__)

EVENT_COMPLETE = "complete"
EVENT_ERROR = "error"
EVENT_PROGRESS = "progress"
EVENT_FILAMENT_LOW = "filament_low"
EVENT_COOLDOWN = "cooldown"
EVENT_OFFLINE = "offline"
EVENT_RECOVERY = "recovery"
EVENT_CUSTOM = "custom"

EVENT_PRIORITY = {
    EVENT_ERROR: 0,
    EVENT_COMPLETE: 1,
    EVENT_PROGRESS: 2,
    EVENT_FILAMENT_LOW: 2,
    EVENT_COOLDOWN: 2,
    EVENT_OFFLINE: 0,
    EVENT_RECOVERY: 1,
    EVENT_CUSTOM: 2,
}


@dataclass
class AlertEvent:
    event_type: str
    serial: str
    printer_name: str
    printer_model: str
    state_summary: str
    message: str
    custom_message: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


def _mute_active(mute_range: str) -> bool:
    if not mute_range or not mute_range.strip():
        return False
    match = re.match(r'^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$', mute_range.strip())
    if not match:
        return False
    start_h, start_m, end_h, end_m = map(int, match.groups())
    start_min = start_h * 60 + start_m
    end_min = end_h * 60 + end_m
    now = time.localtime()
    now_min = now.tm_hour * 60 + now.tm_min
    if start_min < end_min:
        return start_min <= now_min < end_min
    else:
        return now_min >= start_min or now_min < end_min


def _build_state_summary(state: PrinterState) -> str:
    lines = []
    state_labels = {
        STATE_IDLE: "空闲", STATE_RUNNING: "打印中", STATE_PAUSE: "暂停",
        STATE_PREPARE: "准备中", STATE_FINISH: "已完成", STATE_FAILED: "失败",
    }
    label = state_labels.get(state.gcode_state, state.gcode_state)
    lines.append(f"状态: {label}")
    if state.gcode_state == STATE_RUNNING:
        lines.append(f"进度: {state.mc_percent}%")
        if state.mc_remaining_time > 0:
            mins = state.mc_remaining_time // 60
            lines.append(f"剩余: {mins} 分钟")
        if state.total_layer_num > 0:
            lines.append(f"层数: {state.layer_num}/{state.total_layer_num}")
    lines.append(f"喷嘴: {state.nozzle_temper:.0f}°C / {state.nozzle_target_temper:.0f}°C")
    lines.append(f"热床: {state.bed_temper:.0f}°C / {state.bed_target_temper:.0f}°C")
    if state.chamber_temper > 0:
        lines.append(f"腔体: {state.chamber_temper:.0f}°C")
    if state.ams_lowest_remain < 100:
        lines.append(f"耗材最低余量: {state.ams_lowest_remain:.0f}%")
    if state.print_error:
        lines.append(f"错误码: {state.print_error}")
    if state.hms:
        lines.append(f"HMS: {state.hms}")
    return "\n".join(lines)


def _build_native_message(event: AlertEvent) -> str:
    icons = {
        EVENT_COMPLETE: "✅", EVENT_ERROR: "❌", EVENT_PROGRESS: "📊",
        EVENT_FILAMENT_LOW: "🟡", EVENT_COOLDOWN: "❄️",
        EVENT_OFFLINE: "🔴", EVENT_RECOVERY: "🟢",
    }
    icon = icons.get(event.event_type, "📌")
    name = event.printer_name or event.printer_model or event.serial
    return f"🖨️ {name} | {icon} {event.message}\n{event.state_summary}"


def _evaluate_builtin(old: PrinterState, new: PrinterState, alert_delay: int) -> list[AlertEvent]:
    events = []
    name = new.name or new.model or ""
    model = new.model or ""

    if old.gcode_state != STATE_FAILED and new.gcode_state == STATE_FAILED:
        state_summary = _build_state_summary(new)
        msg = f"打印失败"
        if new.print_error:
            msg += f" (错误码: {new.print_error})"
        if new.hms:
            hms_str = ", ".join(str(h) for h in new.hms[:3])
            msg += f" HMS: {hms_str}"
        events.append(AlertEvent(EVENT_ERROR, new.serial, name, model, state_summary, msg))

    if new.gcode_state == STATE_FINISH and old.gcode_state != STATE_FINISH and old.gcode_state != STATE_IDLE:
        state_summary = _build_state_summary(new)
        events.append(AlertEvent(EVENT_COMPLETE, new.serial, name, model, state_summary, "打印完成"))

    if new.gcode_state == STATE_RUNNING and old.gcode_state != new.gcode_state:
        pass

    return events


def _evaluate_progress_nodes(
    old: PrinterState, new: PrinterState, nodes: list[int], alert_delay: int
) -> list[AlertEvent]:
    events = []
    name = new.name or new.model or ""
    model = new.model or ""

    if new.gcode_state != STATE_RUNNING:
        return events

    for node in nodes:
        if old.mc_percent < node <= new.mc_percent:
            state_summary = _build_state_summary(new)
            events.append(AlertEvent(
                EVENT_PROGRESS, new.serial, name, model, state_summary,
                f"打印进度达到 {node}%"
            ))

    return events


def _evaluate_filament(old: PrinterState, new: PrinterState, threshold: int, alert_delay: int) -> list[AlertEvent]:
    events = []
    name = new.name or new.model or ""
    model = new.model or ""

    if new.ams_lowest_remain >= 100 or not new.ams:
        return events
    if new.ams_lowest_remain <= threshold and old.ams_lowest_remain > threshold:
        state_summary = _build_state_summary(new)
        low_trays = []
        for ams in new.ams:
            for tray in ams.trays:
                if tray.remain <= threshold:
                    material = tray.tray_sub_brands or tray.tray_type or "未知"
                    low_trays.append(f"AMS{ams.ams_id}槽{tray.tray_id}: {material} {tray.remain}%")
        detail = "; ".join(low_trays)
        events.append(AlertEvent(
            EVENT_FILAMENT_LOW, new.serial, name, model, state_summary,
            f"耗材不足 ({detail})"
        ))

    return events


def _evaluate_cooldown(old: PrinterState, new: PrinterState, alert_delay: int) -> list[AlertEvent]:
    events = []
    name = new.name or new.model or ""
    model = new.model or ""

    if old.gcode_state != STATE_FINISH and new.gcode_state != STATE_FINISH:
        return events

    cooldown_target = max(new.bed_target_temper, 40.0)

    if old.bed_temper > cooldown_target >= new.bed_temper:
        state_summary = _build_state_summary(new)
        events.append(AlertEvent(
            EVENT_COOLDOWN, new.serial, name, model, state_summary,
            f"热床降温完成 ({new.bed_temper:.0f}°C)"
        ))

    return events


class AlertEngine:
    def __init__(self, config: dict):
        self._config = config
        self._flash = FlashQueue(self)
        self._task_ids: dict[str, set] = {}
        self._custom_last_trigger: dict[str, float] = {}
        self._rule_prev: dict[str, bool] = {}
        self._counters: dict[str, float] = {"print_hours": 0, "completion_count": 0, "failure_consecutive": 0}
        self._last_pushall_time: dict[str, float] = {}
        self._maintenance_trigger: dict[str, float] = {}
        self._on_native: Optional[Callable] = None
        self._on_ai: Optional[Callable] = None
        self._silent_events: dict[str, list[str]] = {}
        self._last_state: dict[str, PrinterState] = {}

    def set_native_callback(self, cb: Callable):
        self._on_native = cb

    def set_ai_callback(self, cb: Callable):
        self._on_ai = cb

    def on_state_change(self, serial: str, old_state: PrinterState, new_state: PrinterState):
        self._last_state[serial] = new_state
        self._evaluate(serial, old_state, new_state)

    def get_silent_history(self, serial: str) -> list[str]:
        return self._silent_events.pop(serial, [])

    def get_counters(self) -> dict:
        return dict(self._counters)

    def load_counters(self, data: dict):
        for k in self._counters:
            self._counters[k] = data.get(k, 0)

    def load_maintenance_triggers(self, data: dict):
        self._maintenance_trigger = data

    def set_counter(self, name: str, value: float):
        if name not in self._counters:
            return False
        self._counters[name] = value
        for serial in self._last_state:
            self._evaluate_maintenance(serial)
        return True

    def _evaluate(self, serial: str, old: PrinterState, new: PrinterState):
        config = self._config
        alert_delay = config.get("monitor", {}).get("alert_delay", 90)
        alerts = config.get("alerts", {})

        if new.task_id and new.task_id != old.task_id:
            self._task_ids.setdefault(serial, set()).discard(old.task_id)

        all_events: list[AlertEvent] = []

        if alerts.get("on_error", True):
            all_events.extend(_evaluate_builtin(old, new, alert_delay))

        if not new.gcode_state == STATE_FAILED:
            if alerts.get("on_complete", True):
                pass

        if alerts.get("on_complete", True):
            if new.gcode_state == STATE_FINISH and old.gcode_state not in (STATE_FINISH, STATE_IDLE):
                name = new.name or new.model or ""
                model = new.model or ""
                all_events.append(AlertEvent(
                    EVENT_COMPLETE, serial, name, model,
                    _build_state_summary(new), "打印完成"
                ))

        if alerts.get("on_error", True):
            if old.gcode_state != STATE_FAILED and new.gcode_state == STATE_FAILED:
                name = new.name or new.model or ""
                model = new.model or ""
                msg = "打印失败"
                if new.print_error:
                    msg += f" (错误码: {new.print_error})"
                if new.hms:
                    hms_str = ", ".join(str(h) for h in new.hms[:3])
                    msg += f" HMS: {hms_str}"
                all_events.append(AlertEvent(
                    EVENT_ERROR, serial, name, model,
                    _build_state_summary(new), msg
                ))

        progress_str = alerts.get("progress_nodes", "50,90")
        try:
            nodes = [int(n.strip()) for n in progress_str.split(",") if n.strip()]
        except ValueError:
            nodes = []
        if alerts.get("on_complete", True) and nodes:
            all_events.extend(_evaluate_progress_nodes(old, new, nodes, alert_delay))

        if alerts.get("on_filament_low", True):
            threshold = alerts.get("filament_threshold", 10)
            all_events.extend(_evaluate_filament(old, new, threshold, alert_delay))

        if alerts.get("on_cooldown", True):
            all_events.extend(_evaluate_cooldown(old, new, alert_delay))

        custom_rules = config.get("custom_rules", [])
        if custom_rules:
            all_events.extend(self._evaluate_custom_rules(old, new, custom_rules))

        self._update_counters(serial, old, new)
        self._evaluate_maintenance(serial)

        if alerts.get("on_offline", True) and old.online and not new.online:
            name = new.name or new.model or ""
            model = new.model or ""
            all_events.append(AlertEvent(
                EVENT_OFFLINE, serial, name, model,
                _build_state_summary(new), "打印机离线"
            ))

        if alerts.get("on_recovery", True) and not old.online and new.online:
            name = new.name or new.model or ""
            model = new.model or ""
            all_events.append(AlertEvent(
                EVENT_RECOVERY, serial, name, model,
                _build_state_summary(new), "打印机已恢复连接"
            ))

        sent_task_ids = self._task_ids.setdefault(serial, set())
        filtered = []
        for e in all_events:
            if e.event_type == EVENT_ERROR:
                filtered.append(e)
                continue
            if new.task_id in sent_task_ids and e.event_type != EVENT_ERROR:
                continue
            filtered.append(e)
        if new.task_id:
            sent_task_ids.add(new.task_id)

        if filtered:
            types = [e.event_type for e in filtered]
            logger.debug(f"[Alert] serial={serial[:12]} events={types}")
        self._flash.process(serial, filtered)

    def _evaluate_custom_rules(self, old: PrinterState, new: PrinterState, rules: list) -> list[AlertEvent]:
        events = []
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            condition = rule.get("condition", "").strip()
            if not condition:
                continue

            try:
                vars_dict = {
                    "gcode_state": new.gcode_state,
                    "mc_percent": new.mc_percent,
                    "mc_remaining_time": new.mc_remaining_time,
                    "nozzle_temper": new.nozzle_temper,
                    "nozzle_target_temper": new.nozzle_target_temper,
                    "bed_temper": new.bed_temper,
                    "bed_target_temper": new.bed_target_temper,
                    "chamber_temper": new.chamber_temper,
                    "layer_num": new.layer_num,
                    "total_layer_num": new.total_layer_num,
                    "print_error": new.print_error,
                    "spd_lvl": new.spd_lvl,
                    "spd_mag": new.spd_mag,
                    "serial": new.serial,
                    "ams_lowest_remain": new.ams_lowest_remain,
                    "gcode_state_old": old.gcode_state,
                    "print_hours": round(self._counters["print_hours"], 1),
                    "completion_count": int(self._counters["completion_count"]),
                    "failure_consecutive": int(self._counters["failure_consecutive"]),
                }
                result = bool(eval(condition, {"__builtins__": {}}, vars_dict))
            except Exception:
                continue

            rule_name = rule.get("name", "")
            key = f"{rule_name}:{new.serial}"
            prev = self._rule_prev.get(key, False)
            self._rule_prev[key] = result
            trigger_mode = rule.get("trigger_mode", "edge")

            if not result:
                continue

            if trigger_mode == "edge":
                if not prev:
                    cooldown = rule.get("cooldown", 300)
                    last = self._custom_last_trigger.get(key, 0)
                    if time.time() - last < cooldown:
                        continue
                    self._custom_last_trigger[key] = time.time()
                else:
                    continue
            else:
                cooldown = rule.get("cooldown", 300)
                last = self._custom_last_trigger.get(key, 0)
                if time.time() - last < max(cooldown, self._config.get("monitor", {}).get("alert_delay", 90)):
                    continue
                self._custom_last_trigger[key] = time.time()

            msg_template = rule.get("message", "")
            try:
                msg = msg_template.format(
                    printer_name=new.name or new.model,
                    printer_model=new.model or "",
                    serial=new.serial,
                    gcode_state=new.gcode_state,
                    mc_percent=new.mc_percent,
                    mc_remaining_time=new.mc_remaining_time,
                    nozzle_temper=new.nozzle_temper,
                    nozzle_target_temper=new.nozzle_target_temper,
                    bed_temper=new.bed_temper,
                    bed_target_temper=new.bed_target_temper,
                    chamber_temper=new.chamber_temper,
                    layer_num=new.layer_num,
                    total_layer_num=new.total_layer_num,
                    print_error=new.print_error,
                    spd_lvl=new.spd_lvl,
                    spd_mag=new.spd_mag,
                    ams_lowest_remain=new.ams_lowest_remain,
                    task_id=new.task_id,
                    gcode_file=new.gcode_file,
                    subtask_name=new.subtask_name,
                    print_hours=round(self._counters["print_hours"], 1),
                    completion_count=int(self._counters["completion_count"]),
                    failure_consecutive=int(self._counters["failure_consecutive"]),
                )
            except Exception:
                msg = msg_template

            events.append(AlertEvent(
                EVENT_CUSTOM, new.serial, new.name or new.model or "",
                new.model or "", _build_state_summary(new),
                f"自定义规则: {rule_name}", custom_message=msg,
            ))

        return events

    def _update_counters(self, serial: str, old: PrinterState, new: PrinterState):
        now = time.time()
        last = self._last_pushall_time.get(serial, now)
        self._last_pushall_time[serial] = now

        if old.gcode_state == STATE_RUNNING and new.gcode_state == STATE_RUNNING:
            elapsed = now - last
            if 0 < elapsed < 600:
                self._counters["print_hours"] += elapsed / 3600.0

        if old.gcode_state != STATE_FINISH and new.gcode_state == STATE_FINISH:
            self._counters["completion_count"] += 1

        if old.gcode_state != STATE_FAILED and new.gcode_state == STATE_FAILED:
            self._counters["failure_consecutive"] += 1
        elif new.gcode_state in (STATE_FINISH, STATE_RUNNING):
            self._counters["failure_consecutive"] = 0

    def _evaluate_maintenance(self, serial: str):
        tasks = self._config.get("maintenance_tasks", [])
        for task in tasks:
            if not task.get("enabled", True):
                continue
            task_type = task.get("type", "hours")
            interval = task.get("interval", 0)
            if not interval:
                continue

            task_id = f"maint:{task.get('name', '')}:{serial}"
            counter_key = "print_hours" if task_type == "hours" else "completion_count"
            current = self._counters.get(counter_key, 0)

            last_trigger = self._maintenance_trigger.get(task_id, 0)
            if int(current // interval) <= int(last_trigger // interval):
                continue

            self._maintenance_trigger[task_id] = current

            task_mute = task.get("mute", "")
            if _mute_active(task_mute):
                continue

            msg = task.get("message", "")
            try:
                msg = msg.format(
                    print_hours=round(self._counters["print_hours"], 1),
                    completion_count=int(self._counters["completion_count"]),
                    failure_consecutive=int(self._counters["failure_consecutive"]),
                )
            except Exception:
                pass

            name = self._last_state.get(serial)
            printer_name = name.name or name.model or serial if name else serial
            self.dispatch(AlertEvent(
                EVENT_CUSTOM, serial, printer_name, name.model if name else "",
                f"维护提醒: {task.get('name')}", f"维护提醒: {task.get('name')}",
                custom_message=msg,
            ))

    def dispatch(self, event: AlertEvent):
        mutes = self._config.get("mutes", {})
        mute_key = f"mute_{event.event_type}"
        mute_range = mutes.get(mute_key, "")
        push_config = self._config.get("push", {})
        mode = push_config.get("mode", "native")

        if _mute_active(mute_range) and event.event_type != EVENT_ERROR:
            self._silent_events.setdefault(event.serial, []).append(
                f"[{time.strftime('%H:%M')}] {event.message}"
            )
            logger.debug(f"[Dispatch] serial={event.serial[:12]} type={event.event_type} MUTED")
            return

        push_config = self._config.get("push", {})
        mode = push_config.get("mode", "native")
        logger.debug(f"[Dispatch] serial={event.serial[:12]} type={event.event_type} mode={mode} native={bool(self._on_native)} ai={bool(self._on_ai)}")

        if mode in ("native", "both"):
            native_msg = _build_native_message(event)
            if event.event_type == EVENT_CUSTOM and event.custom_message:
                native_msg = event.custom_message if not event.custom_message.startswith("🖨️") else event.custom_message
            if self._on_native:
                self._on_native(event.serial, native_msg)

        if mode in ("ai", "both"):
            if self._on_ai:
                self._on_ai(event)


class FlashQueue:
    def __init__(self, engine: AlertEngine):
        self._engine = engine
        self._queues: dict[str, dict] = {}
        self._timers: dict[str, asyncio.Task] = {}

    def process(self, serial: str, events: list[AlertEvent]):
        if not events:
            return

        queue = self._queues.setdefault(serial, {
            "events": {},
            "timer": None,
        })

        has_error = False
        for e in events:
            if e.event_type == EVENT_ERROR:
                has_error = True
                if self._timers.get(serial):
                    self._timers[serial].cancel()
                    self._timers.pop(serial, None)
                self._flush_all(serial, events)
                return
            priority = EVENT_PRIORITY.get(e.event_type, 2)
            existing = queue["events"].get(e.event_type)
            if existing is None or priority <= EVENT_PRIORITY.get(existing.event_type, 2):
                queue["events"][e.event_type] = e

        if EVENT_COMPLETE in queue["events"]:
            for key in list(queue["events"].keys()):
                if key in (EVENT_PROGRESS, EVENT_COOLDOWN):
                    queue["events"].pop(key, None)

        queued_types = list(queue["events"].keys())
        delay = 30 if EVENT_COMPLETE in queue["events"] else self._engine._config.get("monitor", {}).get("alert_delay", 90)
        logger.debug(f"[Flash] serial={serial[:12]} queued={queued_types} delay={delay}s")

        self._reschedule(serial)

    def _reschedule(self, serial: str):
        if serial in self._timers:
            self._timers[serial].cancel()

        queue = self._queues.get(serial, {})
        events_dict = queue.get("events", {})

        has_complete = EVENT_COMPLETE in events_dict
        delay = 30 if has_complete else self._engine._config.get("monitor", {}).get("alert_delay", 90)

        self._timers[serial] = asyncio.create_task(self._delayed_flush(serial, delay))

    async def _delayed_flush(self, serial: str, delay: float):
        await asyncio.sleep(delay)
        await self._flush(serial)

    async def _flush(self, serial: str):
        queue = self._queues.pop(serial, {})
        events_dict = queue.get("events", {})
        logger.debug(f"[Flash] flushing {len(events_dict)} events for {serial[:12]}: {list(events_dict.keys())}")
        for event in events_dict.values():
            self._engine.dispatch(event)
        self._timers.pop(serial, None)

    def _flush_all(self, serial: str, events: list[AlertEvent]):
        self._queues.pop(serial, None)
        if serial in self._timers:
            self._timers[serial].cancel()
            self._timers.pop(serial, None)
        for event in events:
            self._engine.dispatch(event)
