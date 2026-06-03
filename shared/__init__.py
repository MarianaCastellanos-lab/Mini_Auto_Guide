# shared/__init__.py
from .event_bus import EventBus, Event, EventType  # <-- Añadir EventType
from .context_manager import ContextManager, SystemContext
from .data_structures import (
    UserData,
    CameraData,
    StarData,
    CalibrationProfile,
    CorrectionProfile,
    Flags
)

__all__ = [
    'EventBus',
    'Event',
    'EventType',  # <-- Añadir aquí también
    'ContextManager',
    'SystemContext',
    'UserData',
    'CameraData', 
    'StarData',
    'CalibrationProfile',
    'CorrectionProfile',
    'Flags'
]