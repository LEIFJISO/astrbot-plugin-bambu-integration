from typing import Optional
from printer_manager import PrinterManager

_manager: Optional[PrinterManager] = None
_alert_engine = None


def set_manager(mgr: PrinterManager):
    global _manager
    _manager = mgr


def get_manager() -> Optional[PrinterManager]:
    return _manager


def set_alert_engine(engine):
    global _alert_engine
    _alert_engine = engine


def get_alert_engine():
    return _alert_engine
