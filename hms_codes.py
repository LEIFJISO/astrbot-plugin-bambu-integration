import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gzip
import json
from pathlib import Path

HMS_SEVERITY_LEVELS = {
    "default": "未知",
    1: "致命",
    2: "严重",
    3: "一般",
    4: "提示",
}

HMS_MODULES = {
    "default": "未知",
    0x05: "主板",
    0x0C: "xcam",
    0x07: "AMS",
    0x08: "工具头",
    0x03: "运动控制",
}

_DATA_DIR = Path(__file__).parent / "hms_error_text"
_zh_data = None
_en_data = None


def _load_data(lang: str) -> dict:
    path = _DATA_DIR / f"hms_{lang}.json.gz"
    if not path.exists():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _hms_key(attr: int, code: int) -> str:
    return (
        f"{int(attr / 0x10000):0>4X}"
        f"{attr & 0xFFFF:0>4X}"
        f"{int(code / 0x10000):0>4X}"
        f"{code & 0xFFFF:0>4X}"
    )


def get_severity(code: int) -> str:
    sev = (code >> 16) & 0xFF
    return HMS_SEVERITY_LEVELS.get(sev, HMS_SEVERITY_LEVELS["default"])


def get_module(attr: int) -> str:
    mod = (attr >> 24) & 0xFF
    return HMS_MODULES.get(mod, HMS_MODULES["default"])


def lookup_hms(attr: int, code: int, lang: str = "zh") -> dict:
    global _zh_data, _en_data
    if lang == "zh":
        if _zh_data is None:
            _zh_data = _load_data("zh_cn")
        db = _zh_data
    else:
        if _en_data is None:
            _en_data = _load_data("en")
        db = _en_data

    key = _hms_key(attr, code)
    entry = db.get("device_hms", {}).get(key, {})
    text = ""
    if entry:
        for msg, models in entry.items():
            if msg:
                text = msg
                break

    return {
        "severity": get_severity(code),
        "module": get_module(attr),
        "text": text or f"未知 (代码: {code}, 请查阅 Bambu Wiki)",
        "wiki_url": f"https://wiki.bambulab.com/en/hms/home",
    }
