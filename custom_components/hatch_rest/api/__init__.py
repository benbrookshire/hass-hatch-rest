"""Home Assistant-independent client for Hatch WiFi sound machines."""

from .devices import HatchDevice, RestIot, RestMini, Restore, RestPlus, RoutineDevice
from .errors import HatchAuthError, HatchConnectionError, HatchError
from .hub import HatchHub

__all__ = [
    "HatchAuthError",
    "HatchConnectionError",
    "HatchDevice",
    "HatchError",
    "HatchHub",
    "RestIot",
    "RestMini",
    "RestPlus",
    "Restore",
    "RoutineDevice",
]
