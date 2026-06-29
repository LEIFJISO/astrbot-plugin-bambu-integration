import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataclasses import dataclass, field
from typing import Optional, Callable, Any

from astrbot.api import logger

STATE_IDLE = "IDLE"
STATE_RUNNING = "RUNNING"
STATE_PAUSE = "PAUSE"
STATE_PREPARE = "PREPARE"
STATE_FINISH = "FINISH"
STATE_FAILED = "FAILED"
STATE_INIT = "INIT"

_INCREMENTAL_KEEP_KEYS = (
    "gcode_state", "mc_percent", "mc_remaining_time", "layer_num", "total_layer_num",
    "chamber_temper", "print_error", "hms", "spd_lvl", "spd_mag",
    "cooling_fan_speed", "heatbreak_fan_speed", "big_fan1_speed", "big_fan2_speed",
    "wifi_signal", "gcode_file", "task_id", "job_id", "subtask_name",
    "nozzle_diameter", "nozzle_type", "sdcard", "online", "name",
)


@dataclass
class AMSTray:
    tray_id: str = ""
    remain: int = 0
    tray_type: str = ""
    tray_sub_brands: str = ""
    tray_color: str = ""
    tray_weight: str = ""
    empty: bool = False
    cols: list = field(default_factory=list)


@dataclass
class AMSInfo:
    ams_id: str = ""
    humidity: str = ""
    humidity_raw: int = 0
    temp: float = 0.0
    trays: list[AMSTray] = field(default_factory=list)


@dataclass
class PrinterState:
    serial: str = ""
    name: str = ""
    model: str = ""
    gcode_state: str = ""
    mc_percent: int = 0
    mc_remaining_time: int = 0
    nozzle_temper: float = 0.0
    nozzle_target_temper: float = 0.0
    nozzle_temper_left: float = 0.0
    nozzle_target_left: float = 0.0
    is_dual_nozzle: bool = False
    bed_temper: float = 0.0
    bed_target_temper: float = 0.0
    chamber_temper: float = 0.0
    layer_num: int = 0
    total_layer_num: int = 0
    print_error: int = 0
    hms: list = field(default_factory=list)
    spd_lvl: int = 0
    spd_mag: int = 0
    cooling_fan_speed: str = "0"
    heatbreak_fan_speed: str = "0"
    big_fan1_speed: str = "0"
    big_fan2_speed: str = "0"
    wifi_signal: str = ""
    gcode_file: str = ""
    task_id: str = ""
    job_id: str = ""
    subtask_name: str = ""
    nozzle_diameter: str = ""
    nozzle_type: str = ""
    sdcard: bool = False
    online: bool = False
    ams: list[AMSInfo] = field(default_factory=list)
    ams_lowest_remain: float = 100.0
    ams_status: int = 0
    lights_report: list = field(default_factory=list)
    firmware_version: str = ""
    raw: dict = field(default_factory=dict)


def _parse_temperature_standard(data: dict) -> tuple[float, float, float, float]:
    nozzle_current = float(data.get("nozzle_temper", 0) or 0)
    nozzle_target = float(data.get("nozzle_target_temper", 0) or 0)
    bed_current = float(data.get("bed_temper", 0) or 0)
    bed_target = float(data.get("bed_target_temper", 0) or 0)
    return nozzle_current, nozzle_target, bed_current, bed_target


def _parse_temperature_h2d(data: dict) -> tuple[float, float, float, float]:
    nozzle_current = 0.0
    nozzle_target = 0.0
    bed_current = 0.0
    bed_target = 0.0

    extruder_info = data.get("device", {}).get("extruder", {}).get("info", [])
    if extruder_info:
        raw = extruder_info[0].get("temp", 0)
        nozzle_current = float(raw & 0xFFFF)
        nozzle_target = float((raw >> 16) & 0xFFFF)

    bed_info = data.get("device", {}).get("bed", {}).get("info", {})
    raw_bed = bed_info.get("temp", 0)
    if raw_bed:
        bed_current = float(raw_bed & 0xFFFF)
        bed_target = float((raw_bed >> 16) & 0xFFFF)
    else:
        bed_current = float(data.get("bed_temper", 0) or 0)
        bed_target = float(data.get("bed_target_temper", 0) or 0)

    return nozzle_current, nozzle_target, bed_current, bed_target


def _parse_dual_nozzle(data: dict) -> tuple[float, float, bool]:
    extruder_info = data.get("device", {}).get("extruder", {}).get("info", [])
    if len(extruder_info) < 2:
        return 0.0, 0.0, False
    extruder_state = data.get("device", {}).get("extruder", {}).get("state", 0)
    active_idx = (extruder_state >> 4) & 0xF
    left_idx = 1 if active_idx == 0 else 0

    left_raw = extruder_info[left_idx].get("temp", 0) if left_idx < len(extruder_info) else 0
    left_current = float(left_raw & 0xFFFF)
    left_target = float((left_raw >> 16) & 0xFFFF)

    return left_current, left_target, True


def _parse_ams(data: dict) -> list[AMSInfo]:
    ams_data = data.get("ams", {})
    if not ams_data:
        return []

    result = []
    ams_list = ams_data.get("ams", [])
    if not isinstance(ams_list, list):
        return result

    for ams_unit in ams_list:
        info = AMSInfo(
            ams_id=str(ams_unit.get("id", "")),
            humidity=str(ams_unit.get("humidity", "")),
            humidity_raw=int(ams_unit.get("humidity_raw", 0)),
            temp=float(ams_unit.get("temp", 0) or 0),
        )
        for tray in ams_unit.get("tray", []):
            tag_uid = str(tray.get("tag_uid", ""))
            tray_type = str(tray.get("tray_type", ""))
            is_empty = (not tag_uid or tag_uid == "0000000000000000") and not tray_type

            cols = tray.get("cols", [])
            if isinstance(cols, list) and cols:
                color = str(cols[0])
            else:
                color = str(tray.get("tray_color", ""))

            info.trays.append(AMSTray(
                tray_id=str(tray.get("id", "")),
                remain=int(tray.get("remain", 0)) if not is_empty else 0,
                tray_type=tray_type,
                tray_sub_brands=str(tray.get("tray_sub_brands", "")),
                tray_color=color,
                tray_weight=str(tray.get("tray_weight", "")),
                empty=is_empty,
                cols=cols if isinstance(cols, list) else [],
            ))
        result.append(info)
    return result


def _lowest_remain(ams_list: list[AMSInfo]) -> float:
    if not ams_list:
        return 100.0
    remains = [t.remain for a in ams_list for t in a.trays if not t.empty]
    return float(min(remains)) if remains else 100.0


def _is_h2d_model(data: dict) -> bool:
    device = data.get("device", {})
    if device.get("extruder", {}).get("info"):
        return True
    if device.get("bed", {}).get("info", {}).get("temp"):
        return True
    return False


class PrinterManager:
    def __init__(self):
        self._states: dict[str, PrinterState] = {}
        self._initialized: dict[str, bool] = {}
        self._models: dict[str, str] = {}
        self._is_h2d: dict[str, bool] = {}
        self._on_state_change: Optional[Callable] = None

    def set_callback(self, callback: Callable):
        self._on_state_change = callback

    def get_state(self, serial: str) -> Optional[PrinterState]:
        return self._states.get(serial)

    def get_states(self) -> dict[str, PrinterState]:
        return dict(self._states)

    def is_initialized(self, serial: str) -> bool:
        return self._initialized.get(serial, False)

    def set_model(self, serial: str, model: str, name: str = ""):
        self._models[serial] = model
        if serial in self._states:
            self._states[serial].model = model
        if name and serial in self._states:
            self._states[serial].name = name

    def update_from_pushall(self, serial: str, data: dict) -> Optional[PrinterState]:
        msg = data.get("msg", 0)
        is_incremental = msg != 0
        old_state = self._states.get(serial)

        # 机型检测：首次从全量 pushall 中检测，之后记住
        is_h2d = _is_h2d_model(data)
        if is_h2d:
            self._is_h2d[serial] = True
        elif self._is_h2d.get(serial, False):
            is_h2d = True
        logger.debug(f"[State] serial={serial[:12]} msg={msg} incr={is_incremental} h2d={is_h2d}(stored={self._is_h2d.get(serial, False)})")

        if is_h2d:
            nozzle_current, nozzle_target, bed_current, bed_target = _parse_temperature_h2d(data)
            left_current, left_target, is_dual = _parse_dual_nozzle(data)
        else:
            nozzle_current, nozzle_target, bed_current, bed_target = _parse_temperature_standard(data)
            left_current, left_target, is_dual = 0.0, 0.0, False

        ams_list = _parse_ams(data)

        new_state = PrinterState(
            serial=serial,
            name=data.get("name", ""),
            model=self._models.get(serial, ""),
            gcode_state=str(data.get("gcode_state", "")),
            mc_percent=int(data.get("mc_percent", 0)),
            mc_remaining_time=int(data.get("mc_remaining_time", 0)),
            nozzle_temper=nozzle_current,
            nozzle_target_temper=nozzle_target,
            nozzle_temper_left=left_current,
            nozzle_target_left=left_target,
            is_dual_nozzle=is_dual,
            bed_temper=bed_current,
            bed_target_temper=bed_target,
            chamber_temper=float(data.get("chamber_temper", 0) or 0),
            layer_num=int(data.get("layer_num", 0)),
            total_layer_num=int(data.get("total_layer_num", 0)),
            print_error=int(data.get("print_error", 0)),
            hms=data.get("hms", []),
            spd_lvl=int(data.get("spd_lvl", 0)),
            spd_mag=int(data.get("spd_mag", 0)),
            cooling_fan_speed=str(data.get("cooling_fan_speed", "0")),
            heatbreak_fan_speed=str(data.get("heatbreak_fan_speed", "0")),
            big_fan1_speed=str(data.get("big_fan1_speed", "0")),
            big_fan2_speed=str(data.get("big_fan2_speed", "0")),
            wifi_signal=str(data.get("wifi_signal", "")),
            gcode_file=str(data.get("gcode_file", "")),
            task_id=str(data.get("task_id", "")),
            job_id=str(data.get("job_id", "")),
            subtask_name=str(data.get("subtask_name", "")),
            nozzle_diameter=str(data.get("nozzle_diameter", "")),
            nozzle_type=str(data.get("nozzle_type", "")),
            sdcard=bool(data.get("sdcard", False)),
            online=bool(data.get("online", {}).get("ahb", True)) if isinstance(data.get("online"), dict) else True,
            ams=ams_list,
            ams_lowest_remain=_lowest_remain(ams_list),
            lights_report=data.get("lights_report", []),
            raw=data,
        )

        if is_incremental and old_state:
            merged_keys = []
            for key in _INCREMENTAL_KEEP_KEYS:
                if key not in data:
                    setattr(new_state, key, getattr(old_state, key))
                    merged_keys.append(key)
            if merged_keys:
                logger.debug(f"[State] merged {len(merged_keys)} keys from old: {merged_keys[:5]}...")
            if not ams_list:
                new_state.ams = old_state.ams
                new_state.ams_lowest_remain = old_state.ams_lowest_remain
            if "nozzle_temper" not in data:
                new_state.nozzle_temper = old_state.nozzle_temper
                new_state.nozzle_target_temper = old_state.nozzle_target_temper
            if "bed_temper" not in data:
                new_state.bed_temper = old_state.bed_temper
                new_state.bed_target_temper = old_state.bed_target_temper

        self._states[serial] = new_state

        was_init = self._initialized.get(serial, False)
        if not was_init:
            self._initialized[serial] = True
            logger.info(f"[PrinterManager] {serial} 首次初始化: gcode_state={new_state.gcode_state}")
            logger.debug(f"[State] first init, skipping callback for {serial[:12]}")
            return new_state

        if self._on_state_change and old_state:
            if old_state.gcode_state != new_state.gcode_state or old_state.mc_percent != new_state.mc_percent:
                logger.info(f"[PrinterManager] {serial} 状态变化: {old_state.gcode_state}({old_state.mc_percent}%) -> {new_state.gcode_state}({new_state.mc_percent}%)")
            logger.debug(f"[State] callback triggered: old={old_state.gcode_state}->new={new_state.gcode_state}")
            self._on_state_change(serial, old_state, new_state)
        elif not self._on_state_change:
            logger.warning(f"[PrinterManager] {serial} 回调未注册，状态变化被忽略")
        elif not old_state:
            logger.warning(f"[PrinterManager] {serial} 无旧状态，跳过评估")

        return new_state

    def update_firmware_info(self, serial: str, info_data: dict):
        modules = info_data.get("module", [])
        for mod in modules:
            if mod.get("name") == "ota":
                model = mod.get("project_name", "")
                sw_ver = mod.get("sw_ver", "")
                self._models[serial] = model
                if serial in self._states:
                    self._states[serial].model = model
                    self._states[serial].firmware_version = sw_ver
                logger.debug(f"[FW] {serial[:12]} model={model} sw_ver={sw_ver}")
                break

    def mark_offline(self, serial: str):
        if serial in self._states:
            old = self._states[serial]
            if not old.online:
                return
            new = PrinterState(**{k: v for k, v in old.__dict__.items()})
            new.online = False
            self._states[serial] = new
            if self._on_state_change:
                self._on_state_change(serial, old, new)

    def mark_online(self, serial: str):
        if serial in self._states:
            old = self._states[serial]
            if old.online:
                return
            new = PrinterState(**{k: v for k, v in old.__dict__.items()})
            new.online = True
            self._states[serial] = new
            if self._on_state_change:
                self._on_state_change(serial, old, new)
