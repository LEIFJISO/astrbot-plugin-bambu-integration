import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from printer_manager import STATE_IDLE, STATE_RUNNING, STATE_PAUSE
import shared


def _get_default_state(serial: str = ""):
    mgr = shared.get_manager()
    if not mgr:
        return None, "拓竹打印机插件未初始化或未登录"
    if not serial:
        states = mgr.get_states()
        if not states:
            return None, "未发现绑定的打印机"
        serial = next(iter(states.keys()))
    state = mgr.get_state(serial)
    if not state:
        return None, f"未找到序列号为 {serial} 的打印机"
    return state, None


@dataclass
class BambuPrinterStatusTool(FunctionTool[AstrAgentContext]):
    name: str = "bambu_printer_status"
    description: str = "获取拓竹 3D 打印机当前状态简报，包括打印进度、温度和预估剩余时间"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "serial": {"type": "string", "description": "打印机序列号，不填则返回当前默认打印机"},
            },
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        state, err = _get_default_state(kwargs.get("serial", ""))
        if err:
            return err

        state_labels = {
            STATE_IDLE: "空闲", STATE_RUNNING: "打印中", STATE_PAUSE: "暂停",
            "PREPARE": "准备中", "FINISH": "已完成", "FAILED": "失败",
        }
        label = state_labels.get(state.gcode_state, state.gcode_state)
        name = state.name or state.model or state.serial

        lines = [f"打印机: {name}", f"状态: {label}"]
        if state.gcode_state == STATE_RUNNING:
            lines.append(f"进度: {state.mc_percent}%")
            if state.mc_remaining_time > 0:
                lines.append(f"预估剩余时间: {state.mc_remaining_time // 60} 分钟")
            if state.total_layer_num > 0:
                lines.append(f"当前层数: {state.layer_num}/{state.total_layer_num}")
        lines.append(f"喷嘴温度: {state.nozzle_temper:.0f}°C (目标 {state.nozzle_target_temper:.0f}°C)")
        lines.append(f"热床温度: {state.bed_temper:.0f}°C (目标 {state.bed_target_temper:.0f}°C)")
        if state.chamber_temper > 0:
            lines.append(f"腔体温度: {state.chamber_temper:.0f}°C")
        if not state.online:
            lines.append("打印机当前离线")
        if state.print_error:
            lines.append(f"错误码: {state.print_error}")

        return "\n".join(lines)


@dataclass
class BambuPrinterDetailTool(FunctionTool[AstrAgentContext]):
    name: str = "bambu_printer_detail"
    description: str = "获取拓竹打印机详细状态，包括所有温度、风扇速度、耗材信息和错误详情"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "serial": {"type": "string", "description": "打印机序列号，不填则返回当前默认打印机"},
            },
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        state, err = _get_default_state(kwargs.get("serial", ""))
        if err:
            return err

        state_labels = {
            STATE_IDLE: "空闲", STATE_RUNNING: "打印中", STATE_PAUSE: "暂停",
            "PREPARE": "准备中", "FINISH": "已完成", "FAILED": "失败",
        }
        label = state_labels.get(state.gcode_state, state.gcode_state)
        name = state.name or state.model or state.serial

        lines = [
            f"打印机: {name}",
            f"型号: {state.model or '未知'}",
            f"序列号: {state.serial}",
            f"固件版本: {state.firmware_version or '未知'}",
            "",
            f"状态: {label}",
        ]
        if state.gcode_state == STATE_RUNNING:
            lines.append(f"进度: {state.mc_percent}%")
            lines.append(f"层数: {state.layer_num}/{state.total_layer_num}")
            if state.mc_remaining_time > 0:
                lines.append(f"剩余时间: {state.mc_remaining_time // 60} 分钟")
        if state.gcode_file:
            lines.append(f"文件: {state.gcode_file}")
        if state.subtask_name:
            lines.append(f"子任务: {state.subtask_name}")
        lines.extend([
            "",
            "温度:",
            f"  喷嘴: {state.nozzle_temper:.1f}C -> {state.nozzle_target_temper:.0f}C",
            f"  热床: {state.bed_temper:.1f}C -> {state.bed_target_temper:.0f}C",
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
        if not state.online:
            lines.append("打印机当前离线")
        if state.print_error:
            lines.append(f"错误码: {state.print_error}")
        if state.hms:
            lines.append(f"HMS: {state.hms}")
        if state.nozzle_diameter:
            lines.append(f"喷嘴直径: {state.nozzle_diameter}mm")
        if state.nozzle_type:
            lines.append(f"喷嘴类型: {state.nozzle_type}")

        return "\n".join(lines)


@dataclass
class BambuListPrintersTool(FunctionTool[AstrAgentContext]):
    name: str = "bambu_list_printers"
    description: str = "列出当前账号绑定的所有拓竹打印机及其基本状态"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        mgr = shared.get_manager()
        if not mgr:
            return "拓竹打印机插件未初始化或未登录"

        states = mgr.get_states()
        if not states:
            return "未发现绑定的打印机，请检查登录状态"

        state_labels = {
            STATE_IDLE: "空闲", STATE_RUNNING: "打印中", STATE_PAUSE: "暂停",
            "PREPARE": "准备中", "FINISH": "已完成", "FAILED": "失败",
        }

        lines = []
        for i, (serial, state) in enumerate(states.items(), 1):
            name = state.name or state.model or serial
            label = state_labels.get(state.gcode_state, state.gcode_state)
            online = "online" if state.online else "offline"
            progress = f" {state.mc_percent}%" if state.gcode_state == STATE_RUNNING else ""
            lines.append(f"{i}. [{online}] {name} [{label}{progress}]")
            if state.model:
                lines.append(f"   型号: {state.model} | 序列号: {serial}")

        return "\n".join(lines)


@dataclass
class BambuAMSStatusTool(FunctionTool[AstrAgentContext]):
    name: str = "bambu_ams_status"
    description: str = "查询拓竹打印机 AMS 多色供料系统各槽位耗材种类、颜色和余量"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "serial": {"type": "string", "description": "打印机序列号，不填则返回当前默认打印机"},
            },
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        state, err = _get_default_state(kwargs.get("serial", ""))
        if err:
            return err

        if not state.ams:
            return f"打印机 {state.name or state.model or state.serial} 未配备 AMS 系统"

        lines = []
        for ams in state.ams:
            lines.append(f"AMS {ams.ams_id} | 湿度: {ams.humidity} | 温度: {ams.temp}C")
            for tray in ams.trays:
                material = tray.tray_sub_brands or tray.tray_type or "未知"
                bar = "#" * (tray.remain // 10) + "-" * (10 - tray.remain // 10)
                lines.append(f"  槽{tray.tray_id}: {material} | 余量: {tray.remain}% {bar}")
                if tray.tray_color and tray.tray_color not in ("FFFFFFFF", ""):
                    lines.append(f"    颜色: #{tray.tray_color[:6]}")
        lines.append(f"最低余量: {state.ams_lowest_remain}%")

        return "\n".join(lines)
