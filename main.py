import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import asyncio
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

from cloud_api import send_code, login, fetch_mqtt_username, fetch_bindings
from printer_manager import PrinterManager, PrinterState
from mqtt_client import BambuMQTTClient
from alert_engine import AlertEngine, AlertEvent
import shared


@register("astrbot_plugin_bambu_integration", "LiuEnder", "拓竹 3D 打印机集成插件", "1.2.2")
class BambuPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self._config = config
        self._manager = PrinterManager()
        self._alert_engine = AlertEngine(self._config)
        shared.set_manager(self._manager)
        shared.set_alert_engine(self._alert_engine)
        self._mqtt: Optional[BambuMQTTClient] = None
        self._mqtt_task: Optional[asyncio.Task] = None
        self._pending_login: Optional[dict] = None
        self._tools_registered = False

        self._alert_engine.set_native_callback(self._on_native_push)
        self._alert_engine.set_ai_callback(self._on_ai_push)
        self._manager.set_callback(self._alert_engine.on_state_change)

    # ========== lifecycle ==========

    async def initialize(self):
        self._state_path = os.path.join("data", "bambu_state.json")
        self._load_state()
        asyncio.create_task(self._periodic_save())
        token = self._config.get("cloud", {}).get("access_token", "")
        if token and not self._tools_registered and self._config.get("push", {}).get("enable_llm_tools", True):
            await self._register_tools()
        if token and self._config.get("monitor", {}).get("enabled", True):
            await self._start_mqtt()

    async def terminate(self):
        self._save_state()
        await self._stop_mqtt()

    def _load_state(self):
        try:
            with open(self._state_path, "r") as f:
                data = json.load(f)
            self._alert_engine.load_counters(data.get("counters", {}))
            self._alert_engine.load_maintenance_triggers(data.get("maintenance_triggers", {}))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_state(self):
        data = {
            "counters": self._alert_engine.get_counters(),
            "maintenance_triggers": self._alert_engine._maintenance_trigger,
        }
        try:
            os.makedirs("data", exist_ok=True)
            with open(self._state_path, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    async def _periodic_save(self):
        while True:
            await asyncio.sleep(300)
            self._save_state()

    # ========== tools ==========

    async def _register_tools(self):
        if self._tools_registered:
            return
        try:
            from tools.printer_tools import (
                BambuPrinterStatusTool, BambuPrinterDetailTool,
                BambuListPrintersTool, BambuAMSStatusTool,
            )
            self.context.add_llm_tools(
                BambuPrinterStatusTool(),
                BambuPrinterDetailTool(),
                BambuListPrintersTool(),
                BambuAMSStatusTool(),
            )
            self._tools_registered = True
        except Exception as e:
            logger.warning(f"注册 LLM 工具失败: {e}")

    # ========== MQTT ==========

    async def _start_mqtt(self):
        cloud = self._config.get("cloud", {})
        region = cloud.get("region", "cn")
        token = cloud.get("access_token", "")
        if not token:
            logger.info("未配置 access_token，跳过 MQTT 连接")
            return
        await self._connect_mqtt(region, token)

    async def _connect_mqtt(self, region: str, token: str):
        username_result = await fetch_mqtt_username(region, token)
        username = username_result.get("username", "u_unknown") if username_result.get("ok") else "u_unknown"

        self._mqtt = BambuMQTTClient(region, username, token, self._manager)
        self._mqtt.set_offline_callback(lambda s: logger.info(f"打印机 {s} 进入离线计时"))
        self._mqtt.set_recovery_callback(lambda s: logger.info(f"打印机 {s} 恢复连接"))

        bindings = await fetch_bindings(region, token)
        if bindings.get("ok"):
            serials = []
            for p in bindings["printers"]:
                serials.append(p["serial"])
                self._manager.set_model(p["serial"], p["model"], p["name"])
            self._mqtt.set_serials(serials)

        self._mqtt_task = asyncio.create_task(self._mqtt.start())

    async def _stop_mqtt(self):
        if self._mqtt:
            await self._mqtt.stop()
            self._mqtt = None
        if self._mqtt_task:
            self._mqtt_task.cancel()
            self._mqtt_task = None

    # ========== messaging ==========

    def _get_notify_targets(self) -> list[str]:
        raw = self._config.get("notify", {}).get("session_id", "")
        return [s.strip() for s in raw.split(",") if s.strip()]

    async def _send_to_session(self, text: str):
        for umo in self._get_notify_targets():
            try:
                chain = MessageChain().message(text)
                await self.context.send_message(umo, chain)
            except Exception as e:
                logger.warning(f"发送通知到 {umo} 失败: {e}")

    async def _on_native_push(self, serial: str, message: str):
        asyncio.create_task(self._send_to_session(message))

    async def _on_ai_push(self, event: AlertEvent):
        push_config = self._config.get("push", {})
        template = push_config.get("ai_prompt", "")
        if not template:
            template = (
                "用户的打印机状态发生了变化，请根据以下数据用简洁自然的中文告知用户：\n\n"
                "打印机：{printer_name}（{printer_model}）\n"
                "事件：{event_type}\n"
                "状态数据：{state_summary}\n\n"
                "要求：简洁自然，无需问候语。故障类事件着重提醒错误信息。"
                "不要编造或猜测数据中没有的信息。"
            )
        prompt = template.format(
            printer_name=event.printer_name,
            printer_model=event.printer_model,
            event_type=event.message,
            state_summary=event.state_summary,
        )
        targets = self._get_notify_targets()
        if not targets:
            return
        try:
            umo = targets[0]
            provider_id = await self.context.get_current_chat_provider_id(umo=umo)
            if not provider_id:
                await self._send_to_session(
                    f"{event.printer_name} | {event.message}\n{event.state_summary}"
                )
                return
            llm_resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            ai_text = llm_resp.completion_text if llm_resp and hasattr(llm_resp, "completion_text") else str(llm_resp)
            for umo in targets:
                chain = MessageChain().message(ai_text)
                await self.context.send_message(umo, chain)
        except Exception as e:
            logger.warning(f"AI 推送失败，降级到原生推送: {e}")
            await self._send_to_session(
                f"{event.printer_name} | {event.message}\n{event.state_summary}"
            )

    # ========== format helpers ==========

    def _format_brief(self, state: PrinterState) -> str:
        labels = {
            "IDLE": "空闲", "RUNNING": "打印中", "PAUSE": "暂停",
            "PREPARE": "准备中", "FINISH": "已完成", "FAILED": "失败",
        }
        name = state.name or state.model or state.serial
        label = labels.get(state.gcode_state, state.gcode_state)
        lines = [f"{name} | {label}"]
        if state.gcode_state == "RUNNING":
            lines.append(f"  进度 {state.mc_percent}%")
            if state.mc_remaining_time > 0:
                lines.append(f"  剩余 {state.mc_remaining_time // 60} 分钟")
            if state.total_layer_num > 0:
                lines.append(f"  层数 {state.layer_num}/{state.total_layer_num}")
        lines.append(f"  喷嘴 {state.nozzle_temper:.0f}C / 热床 {state.bed_temper:.0f}C")
        if state.chamber_temper > 0:
            lines.append(f"  腔体 {state.chamber_temper:.0f}C")
        if state.ams_lowest_remain < 100:
            lines.append(f"  耗材最低 {state.ams_lowest_remain:.0f}%")
        if state.print_error:
            lines.append(f"  错误码: {state.print_error}")
        if not state.online:
            lines.append("  离线")
        return "\n".join(lines)

    def _format_detail(self, state: PrinterState) -> str:
        labels = {
            "IDLE": "空闲", "RUNNING": "打印中", "PAUSE": "暂停",
            "PREPARE": "准备中", "FINISH": "已完成", "FAILED": "失败",
        }
        name = state.name or state.model or state.serial
        label = labels.get(state.gcode_state, state.gcode_state)
        lines = [
            f"{name}",
            f"  型号: {state.model or '未知'}",
            f"  序列号: {state.serial}",
            f"  固件: {state.firmware_version or '未知'}",
            "",
            f"状态: {label}",
        ]
        if state.gcode_state == "RUNNING":
            lines.append(f"进度: {state.mc_percent}%")
            lines.append(f"层数: {state.layer_num}/{state.total_layer_num}")
            if state.mc_remaining_time > 0:
                lines.append(f"剩余: {state.mc_remaining_time // 60} 分钟")
        if state.gcode_file:
            lines.append(f"文件: {state.gcode_file}")
        lines.extend([
            "",
            "温度:",
            f"  喷嘴: {state.nozzle_temper:.1f} -> {state.nozzle_target_temper:.0f}C",
            f"  热床: {state.bed_temper:.1f} -> {state.bed_target_temper:.0f}C",
        ])
        if state.chamber_temper > 0:
            lines.append(f"  腔体: {state.chamber_temper:.1f}C")
        lines.extend([
            "",
            f"风扇: 冷却{state.cooling_fan_speed} 散热{state.heatbreak_fan_speed} 辅助{state.big_fan1_speed}",
        ])
        if state.spd_lvl > 0:
            speed_names = {1: "静音", 2: "标准", 3: "运动", 4: "狂暴"}
            lines.append(f"速度: {speed_names.get(state.spd_lvl, str(state.spd_lvl))} ({state.spd_mag}%)")
        if state.wifi_signal:
            lines.append(f"WiFi: {state.wifi_signal}")
        if state.ams:
            lines.append("")
            for ams in state.ams:
                lines.append(f"AMS {ams.ams_id}: {ams.humidity} {ams.temp}C")
                for tray in ams.trays:
                    material = tray.tray_sub_brands or tray.tray_type or "未知"
                    bar = "#" * (tray.remain // 10) + "-" * (10 - tray.remain // 10)
                    lines.append(f"  槽{tray.tray_id}: {material} {tray.remain}% {bar}")
        if state.ams_lowest_remain < 100:
            lines.append(f"最低余量: {state.ams_lowest_remain}%")
        if state.print_error:
            lines.append(f"错误码: {state.print_error}")
        if state.hms:
            lines.append(f"HMS: {state.hms}")
        if not state.online:
            lines.append("离线")
        return "\n".join(lines)

    # ========== commands ==========

    @filter.command_group("bambu")
    def bambu(self):
        pass

    @bambu.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "拓竹打印机插件指令：\n"
            "  /bambu login [账号] - 登录打印机账号\n"
            "  /bambu verify <code> - 提交验证码\n"
            "  /bambu logout - 清除登录\n"
            "  /bambu status - 查看连接状态\n"
            "  /bambu printers - 打印机列表\n"
            "  /bambu info - 状态简报\n"
            "  /bambu detail - 详细状态\n"
            "  /bambu alert - 查看提醒设置\n"
            "  /bambu alert progress <节点> - 进度节点\n"
            "  /bambu alert <类型> on/off - 提醒开关\n"
            "  /bambu alert filament <阈值> - 耗材阈值\n"
            "  /bambu mute - 查看静默\n"
            "  /bambu mute <类型> <HH:MM> <HH:MM> - 设置静默\n"
            "  /bambu mute <类型> off - 取消静默\n"
            "  /bambu rules - 自定义规则\n"
            "  /bambu rule add/set/del/on/off/test <名字> - 管理规则\n"
            "  /bambu rule vars - 可用变量\n"
            "  /bambu counters - 查看计数器\n"
            "  /bambu counter set <名称> <值> - 设置计数器\n"
            "  /bambu maintenance - 维护任务\n"
            "  /bambu maintenance skip <名称> - 跳过下次提醒\n"
            "  /bambu maintenance mute <名称> <HH:MM> <HH:MM> - 设置静默\n"
            "  /bambu help - 帮助\n"
            "\n登录方式：\n"
            "\n登录方式：\n"
            "  1. 在 WebUI 配置页面直接填写 Access Token\n"
            "  2. 使用 /bambu login 交互登录（推荐）\n"
        )

    @bambu.command("login")
    async def cmd_login(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        cloud = self._config.get("cloud", {})
        region = cloud.get("region", "cn")

        account = ""
        for prefix in ("/bambu login", "bambu login", "login"):
            if msg.startswith(prefix):
                account = msg[len(prefix):].strip()
                break

        if not account:
            self._pending_login = {"region": region, "step": "wait_account"}
            yield event.plain_result(
                "登录流程：\n"
                f"  1. 发送 /bambu login <手机号或邮箱> 开始\n"
                f"  2. 收到验证码后发送 /bambu verify <验证码>\n"
                f"\n"
                f"  当前区域：{region}\n"
                f"  切换区域请在 WebUI 配置页面修改"
            )
            return

        account = account.split()[0]
        self._pending_login = {"region": region, "account": account, "step": "wait_code"}
        yield event.plain_result(f"正在向 {account} 发送验证码...")
        result = await send_code(region, account)
        if result.get("ok"):
            yield event.plain_result(f"验证码已发送至 {account}\n请发送 /bambu verify <验证码> 完成登录")
        else:
            yield event.plain_result(f"发送验证码失败：{result.get('message')}")

    @bambu.command("verify")
    async def cmd_verify(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        args = ""
        for prefix in ("/bambu verify", "bambu verify", "verify"):
            if msg.startswith(prefix):
                args = msg[len(prefix):].strip()
                break

        if not args:
            yield event.plain_result("请输入验证码：/bambu verify <验证码>")
            return

        if not self._pending_login or self._pending_login.get("step") != "wait_code":
            yield event.plain_result("没有正在进行的登录流程，请先 /bambu login <账号>")
            return

        code = args.split()[0]
        region = self._pending_login["region"]
        account = self._pending_login["account"]

        yield event.plain_result("正在登录...")
        login_result = await login(region, account, code)
        if not login_result.get("ok"):
            yield event.plain_result(f"登录失败：{login_result.get('message')}")
            self._pending_login = None
            return

        token = login_result["token"]
        self._config["cloud"]["access_token"] = token
        self._config["cloud"]["account"] = account

        username_result = await fetch_mqtt_username(region, token)
        if username_result.get("ok"):
            logger.info(f"MQTT username: {username_result['username']}")

        umo = event.unified_msg_origin
        self._config["notify"]["session_id"] = umo
        self._config.save_config()

        bindings = await fetch_bindings(region, token)
        if bindings.get("ok"):
            printers = bindings["printers"]
            serials = [p["serial"] for p in printers]
            self._config.setdefault("cloud", {})["serials"] = serials
            lines = [f"登录成功！找到 {len(printers)} 台打印机："]
            for i, p in enumerate(printers, 1):
                online = "online" if p["online"] else "offline"
                lines.append(f"  {i}. [{online}] {p['name']} ({p['model']})")
        else:
            lines = ["登录成功！未找到打印机绑定"]

        self._pending_login = None
        await self._register_tools()
        await self._start_mqtt()
        yield event.plain_result("\n".join(lines))

    @bambu.command("logout")
    async def cmd_logout(self, event: AstrMessageEvent):
        await self._stop_mqtt()
        self._config["cloud"]["access_token"] = ""
        self._config["notify"]["session_id"] = ""
        self._config.save_config()
        self._manager = PrinterManager()
        self._manager.set_callback(self._alert_engine.on_state_change)
        self._tools_registered = False
        shared.set_manager(self._manager)
        yield event.plain_result("已登出，连接已断开")

    @bambu.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        cloud = self._config.get("cloud", {})
        token = cloud.get("access_token", "")
        mqtt_connected = self._mqtt.connected if self._mqtt else False
        last_error = self._mqtt.last_error if self._mqtt else ""
        printer_count = len(self._manager.get_states())
        push_mode = self._config.get("push", {}).get("mode", "native")

        lines = [
            f"登录状态：{'已登录' if token else '未登录'}",
            f"账号：{cloud.get('account', '未设置')}",
            f"区域：{cloud.get('region', 'cn')}",
            f"MQTT 连接：{'已连接' if mqtt_connected else '未连接'}",
        ]
        if last_error and not mqtt_connected:
            lines.append(f"MQTT 错误：{last_error[:80]}")
        lines.extend([
            f"推送模式：{push_mode}",
            f"LLM 工具：{'已注册' if self._tools_registered else '未注册'}",
            f"监控打印机：{printer_count} 台",
        ])
        yield event.plain_result("\n".join(lines))

    @bambu.command("printers")
    async def cmd_printers(self, event: AstrMessageEvent):
        states = self._manager.get_states()
        if not states:
            yield event.plain_result("未发现打印机，请检查登录状态")
            return

        labels = {
            "IDLE": "空闲", "RUNNING": "打印中", "PAUSE": "暂停",
            "PREPARE": "准备中", "FINISH": "已完成", "FAILED": "失败",
        }
        lines = []
        for i, (serial, state) in enumerate(states.items(), 1):
            name = state.name or state.model or serial
            online = "online" if state.online else "offline"
            label = labels.get(state.gcode_state, state.gcode_state)
            progress = f" {state.mc_percent}%" if state.gcode_state == "RUNNING" else ""
            lines.append(f"{i}. [{online}] {name} [{label}{progress}]")
            lines.append(f"   {state.model or '未知'} | {serial}")
        yield event.plain_result("\n".join(lines))

    @bambu.command("info")
    async def cmd_info(self, event: AstrMessageEvent):
        states = self._manager.get_states()
        if not states:
            yield event.plain_result("未发现打印机，请检查登录状态")
            return
        for serial, state in states.items():
            text = self._format_brief(state)
            silent_history = self._alert_engine.get_silent_history(serial)
            if silent_history:
                text += f"\n\n[静默期间经历: {'; '.join(silent_history)}]"
            c = self._alert_engine.get_counters()
            text += f"\n累计: {c['print_hours']:.1f}h | 完成: {int(c['completion_count'])}次"
            yield event.plain_result(text)

    @bambu.command("detail")
    async def cmd_detail(self, event: AstrMessageEvent):
        states = self._manager.get_states()
        if not states:
            yield event.plain_result("未发现打印机")
            return
        for serial, state in states.items():
            yield event.plain_result(self._format_detail(state))

    @bambu.command("alert")
    async def cmd_alert(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        args = ""
        for prefix in ("/bambu alert", "bambu alert", "alert"):
            if msg.startswith(prefix):
                args = msg[len(prefix):].strip()
                break
        alerts = self._config.get("alerts", {})
        mutes = self._config.get("mutes", {})

        if args.startswith("progress "):
            nodes_str = args.replace("progress ", "", 1).strip()
            if not nodes_str:
                yield event.plain_result("请指定进度节点，如 25,50,75,90")
                return
            alerts["progress_nodes"] = nodes_str
            self._config["alerts"] = alerts
            self._config.save_config()
            yield event.plain_result(f"进度节点已设置为：{nodes_str}")
            return

        if args.startswith("filament "):
            try:
                val = int(args.replace("filament ", "", 1).strip())
                alerts["filament_threshold"] = val
                self._config["alerts"] = alerts
                self._config.save_config()
                yield event.plain_result(f"耗材阈值已设置为：{val}%")
                return
            except ValueError:
                yield event.plain_result("请输入数值：/bambu alert filament <百分比>")
                return

        alert_keys_map = {
            "complete": "on_complete", "error": "on_error",
            "filament": "on_filament_low", "cooldown": "on_cooldown",
            "offline": "on_offline", "recovery": "on_recovery",
        }
        for key, config_key in alert_keys_map.items():
            for suffix in (" on", " off"):
                if args == f"{key}{suffix}":
                    alerts[config_key] = (suffix == " on")
                    self._config["alerts"] = alerts
                    self._config.save_config()
                    yield event.plain_result(f"{key} 提醒已{'启用' if alerts[config_key] else '禁用'}")
                    return

        state_labels = {
            "on_complete": "打印完成", "on_error": "打印失败", "on_filament_low": "耗材不足",
            "on_cooldown": "热床降温", "on_offline": "打印机离线", "on_recovery": "打印机恢复",
        }
        lines = ["提醒设置："]
        for key, label in state_labels.items():
            enabled = "启用" if alerts.get(key, True) else "禁用"
            mute_key = f"mute_{key.replace('on_', '')}"
            mute_val = mutes.get(mute_key, "")
            mute_info = f" 静默: {mute_val}" if mute_val else ""
            lines.append(f"  {enabled} {label}{mute_info}")
        lines.append(f"  进度节点：{alerts.get('progress_nodes', '50,90')}")
        lines.append(f"  耗材阈值：{alerts.get('filament_threshold', 10)}%")
        yield event.plain_result("\n".join(lines))

    @bambu.command("mute")
    async def cmd_mute(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        args = ""
        for prefix in ("/bambu mute", "bambu mute", "mute"):
            if msg.startswith(prefix):
                args = msg[len(prefix):].strip()
                break
        mutes = self._config.get("mutes", {})

        mute_keys_map = {
            "complete": "mute_complete", "error": "mute_error",
            "filament": "mute_filament", "progress": "mute_progress",
            "cooldown": "mute_cooldown", "offline": "mute_offline",
            "recovery": "mute_recovery",
        }

        if args == "off":
            for k in mute_keys_map.values():
                mutes[k] = ""
            self._config["mutes"] = mutes
            self._config.save_config()
            yield event.plain_result("已取消所有静默")
            return

        for key, config_key in mute_keys_map.items():
            if args.startswith(f"{key} off"):
                mutes[config_key] = ""
                self._config["mutes"] = mutes
                self._config.save_config()
                yield event.plain_result(f"{key} 静默已取消")
                return
            if args.startswith(key + " "):
                tr = args[len(key) + 1:].strip()
                if not tr:
                    yield event.plain_result("格式：/bambu mute <类型> <HH:MM> <HH:MM>")
                    return
                parts = tr.split()
                mutes[config_key] = f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else tr
                self._config["mutes"] = mutes
                self._config.save_config()
                yield event.plain_result(f"{key} 静默时段已设置为：{mutes[config_key]}")
                return

        items = [
            ("完成", "mute_complete"), ("失败", "mute_error"),
            ("耗材", "mute_filament"), ("进度", "mute_progress"),
            ("降温", "mute_cooldown"), ("离线", "mute_offline"),
            ("恢复", "mute_recovery"),
        ]
        lines = ["静默设置："]
        for label, key in items:
            val = mutes.get(key, "")
            lines.append(f"  {label}：{val if val else '不静默'}")
        yield event.plain_result("\n".join(lines))

    @bambu.command("rules")
    async def cmd_rules(self, event: AstrMessageEvent):
        rules = self._config.get("custom_rules", [])
        if not rules:
            yield event.plain_result("未配置自定义规则。可在 WebUI 配置页面添加")
            return
        lines = ["自定义规则："]
        for i, rule in enumerate(rules):
            enabled = "启用" if rule.get("enabled", True) else "禁用"
            mute = f" 静默: {rule.get('mute')}" if rule.get("mute") else ""
            lines.append(f"  [{i + 1}] {enabled} {rule.get('name', '未命名')}{mute}")
            lines.append(f"      条件：{rule.get('condition', '')}")
            lines.append(f"      冷却：{rule.get('cooldown', 300)}s")
        yield event.plain_result("\n".join(lines))

    @bambu.command("rule")
    async def cmd_rule(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        args = ""
        for prefix in ("/bambu rule", "bambu rule", "rule"):
            if msg.startswith(prefix):
                args = msg[len(prefix):].strip()
                break
        rules = self._config.get("custom_rules", [])

        parts = args.split(maxsplit=1)
        action = parts[0]

        if action == "vars":
            yield event.plain_result(
                "可用变量：\n"
                "  gcode_state - 状态 (IDLE/RUNNING/PAUSE/PREPARE/FINISH/FAILED)\n"
                "  mc_percent - 进度 (0-100)\n"
                "  mc_remaining_time - 剩余时间(秒)\n"
                "  nozzle_temper / nozzle_target_temper - 喷嘴温度\n"
                "  bed_temper / bed_target_temper - 热床温度\n"
                "  chamber_temper - 腔体温度\n"
                "  layer_num / total_layer_num - 层数\n"
                "  print_error - 错误码\n"
                "  spd_lvl / spd_mag - 速度档位/倍率\n"
                "  serial - 序列号\n"
                "  ams_lowest_remain - AMS 最低余量\n"
                "  gcode_state_old - 上一次状态\n"
                "  print_hours - 累计打印小时\n"
                "  completion_count - 打印完成次数\n"
                "  failure_consecutive - 连续失败次数"
            )
            return

        if len(parts) < 2:
            yield event.plain_result(
                "用法：\n"
                "  /bambu rule add <名字>\n"
                "  /bambu rule set <名字> <字段> <值>\n"
                "  /bambu rule del <名字>\n"
                "  /bambu rule on/off <名字>\n"
                "  /bambu rule test <名字>\n"
                "  /bambu rule vars"
            )
            return

        target = parts[1] if len(parts) > 1 else ""

        if action == "add":
            rules.append({
                "name": target, "enabled": True,
                "condition": "", "message": "",
                "mute": "", "cooldown": 300,
            })
            self._config["custom_rules"] = rules
            self._config.save_config()
            yield event.plain_result(f"已创建规则：{target}")
            return

        if action in ("on", "off"):
            for rule in rules:
                if rule.get("name") == target:
                    rule["enabled"] = (action == "on")
                    self._config["custom_rules"] = rules
                    self._config.save_config()
                    yield event.plain_result(f"规则 {target} 已{'启用' if action == 'on' else '禁用'}")
                    return
            yield event.plain_result(f"未找到规则：{target}")
            return

        if action == "del":
            self._config["custom_rules"] = [r for r in rules if r.get("name") != target]
            self._config.save_config()
            yield event.plain_result(f"已删除规则：{target}")
            return

        if action == "set":
            set_parts = target.split(maxsplit=2)
            if len(set_parts) < 2:
                yield event.plain_result("用法：/bambu rule set <名字> <字段> <值>")
                return
            rule_name, field = set_parts[0], set_parts[1]
            value = set_parts[2] if len(set_parts) > 2 else ""
            valid_fields = ("condition", "message", "cooldown", "mute")
            if field not in valid_fields:
                yield event.plain_result(f"支持的字段：{', '.join(valid_fields)}")
                return
            for rule in rules:
                if rule.get("name") == rule_name:
                    if field == "cooldown":
                        try:
                            rule[field] = int(value)
                        except ValueError:
                            yield event.plain_result("cooldown 需要整数(秒)")
                            return
                    else:
                        rule[field] = value
                    self._config["custom_rules"] = rules
                    self._config.save_config()
                    yield event.plain_result(f"规则 {rule_name} 的 {field} 已更新")
                    return
            yield event.plain_result(f"未找到规则：{rule_name}")
            return

        if action == "test":
            states = self._manager.get_states()
            if not states:
                yield event.plain_result("无打印机数据，请检查连接")
                return
            for rule in rules:
                if rule.get("name") == target:
                    condition = rule.get("condition", "").strip()
                    if not condition:
                        yield event.plain_result("该规则无条件表达式")
                        return
                    for serial, state in states.items():
                        vars_dict = {
                            "gcode_state": state.gcode_state,
                            "mc_percent": state.mc_percent,
                            "mc_remaining_time": state.mc_remaining_time,
                            "nozzle_temper": state.nozzle_temper,
                            "nozzle_target_temper": state.nozzle_target_temper,
                            "bed_temper": state.bed_temper,
                            "bed_target_temper": state.bed_target_temper,
                            "chamber_temper": state.chamber_temper,
                            "layer_num": state.layer_num,
                            "total_layer_num": state.total_layer_num,
                            "print_error": state.print_error,
                            "spd_lvl": state.spd_lvl,
                            "spd_mag": state.spd_mag,
                            "serial": state.serial,
                            "ams_lowest_remain": state.ams_lowest_remain,
                        }
                        try:
                            result = eval(condition, {"__builtins__": {}}, vars_dict)
                            yield event.plain_result(
                                f"规则 {target} 测试：{'触发' if result else '未触发'}\n"
                                f"条件：{condition}\n"
                                f"当前：gcode_state={state.gcode_state}, "
                                f"mc_percent={state.mc_percent}, "
                                f"bed_temper={state.bed_temper:.0f}"
                            )
                        except Exception as e:
                            yield event.plain_result(f"规则错误：{e}")
                    return
            yield event.plain_result(f"未找到规则：{target}")

    @bambu.command("counters")
    async def cmd_counters(self, event: AstrMessageEvent):
        c = self._alert_engine.get_counters()
        yield event.plain_result(
            f"累计打印：{c['print_hours']:.1f} h\n"
            f"完成次数：{int(c['completion_count'])} 次\n"
            f"连续失败：{int(c['failure_consecutive'])} 次"
        )

    @bambu.command("counter")
    async def cmd_counter(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        args = ""
        for prefix in ("/bambu counter", "bambu counter", "counter"):
            if msg.startswith(prefix):
                args = msg[len(prefix):].strip()
                break

        parts = args.split(maxsplit=2)
        if len(parts) < 2 or parts[0] != "set":
            yield event.plain_result("用法：/bambu counter set <名称> <值>\n可用：print_hours, completion_count, failure_consecutive")
            return

        name, value_str = parts[1], parts[2] if len(parts) > 2 else "0"
        try:
            value = float(value_str)
        except ValueError:
            yield event.plain_result("值必须是数字")
            return

        if not self._alert_engine.set_counter(name, value):
            yield event.plain_result(f"未知计数器：{name}")
            return

        self._save_state()
        yield event.plain_result(f"计数器 {name} 已设为 {value}")

    @bambu.command("maintenance")
    async def cmd_maintenance(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        args = ""
        for prefix in ("/bambu maintenance", "bambu maintenance", "maintenance"):
            if msg.startswith(prefix):
                args = msg[len(prefix):].strip()
                break

        tasks = self._config.get("maintenance_tasks", [])
        c = self._alert_engine.get_counters()

        if args.startswith("skip "):
            name = args.replace("skip ", "", 1).strip()
            for task in tasks:
                if task.get("name") == name:
                    counter_key = "print_hours" if task.get("type", "hours") == "hours" else "completion_count"
                    current = c.get(counter_key, 0)
                    for key in list(self._alert_engine._maintenance_trigger.keys()):
                        if key.startswith(f"maint:{name}:"):
                            self._alert_engine._maintenance_trigger[key] = current
                            self._save_state()
                            yield event.plain_result(f"已跳过 {name} 下次提醒，基准前移至 {current:.1f}")
                            return
                    yield event.plain_result(f"未找到维护任务：{name}")
                    return
            yield event.plain_result(f"未找到维护任务：{name}")
            return

        if args.startswith("mute "):
            rest = args.replace("mute ", "", 1).strip()
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                yield event.plain_result("用法：/bambu maintenance mute <名称> <HH:MM> <HH:MM> 或 off")
                return
            name, tr = parts[0], parts[1].strip()
            for task in tasks:
                if task.get("name") == name:
                    if tr == "off":
                        task["mute"] = ""
                    else:
                        tparts = tr.split()
                        task["mute"] = f"{tparts[0]}-{tparts[1]}" if len(tparts) >= 2 else tr
                    self._config["maintenance_tasks"] = tasks
                    self._config.save_config()
                    yield event.plain_result(f"{name} 静默已{'取消' if tr == 'off' else '设置为 ' + task['mute']}")
                    return
            yield event.plain_result(f"未找到维护任务：{name}")
            return

        if not tasks:
            yield event.plain_result("未配置维护任务。可在 WebUI 配置页面添加")
            return

        lines = [f"累计打印：{c['print_hours']:.1f}h | 完成：{int(c['completion_count'])}次\n"]
        for task in tasks:
            enabled = "启用" if task.get("enabled", True) else "禁用"
            tt = task.get("type", "hours")
            interval = task.get("interval", 0)
            counter_key = "print_hours" if tt == "hours" else "completion_count"
            current = c.get(counter_key, 0)
            found_trigger = False
            for key, val in self._alert_engine._maintenance_trigger.items():
                if key.startswith(f"maint:{task.get('name')}:"):
                    next_at = (int(val // interval) + 1) * interval if interval else 0
                    remaining = max(0, next_at - current)
                    lines.append(f"  [{enabled}] {task.get('name')} ({tt}, 每{interval} | 下次: {remaining:.0f}{'h' if tt == 'hours' else '次'}后)")
                    found_trigger = True
                    break
            if not found_trigger:
                lines.append(f"  [{enabled}] {task.get('name')} ({tt}, 每{interval})")
            mute = task.get("mute", "")
            if mute:
                lines[-1] += f" 静默: {mute}"
        yield event.plain_result("\n".join(lines))
