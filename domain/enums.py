# domain/enums.py
"""
Enumeraciones del dominio.
"""

from enum import Enum, auto


class Direction(Enum):
    """Direcciones de movimiento en la montura."""
    RA_PLUS = "RA+"
    RA_MINUS = "RA-"
    DEC_PLUS = "DEC+"
    DEC_MINUS = "DEC-"


class CalibrationStatus(Enum):
    """Estados del proceso de calibración."""
    IDLE = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()


class ConnectionStatus(Enum):
    """Estados de conexión con hardware."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STREAMING = "streaming"
    ERROR = "error"


class GuidingStatus(Enum):
    """Estados del guiado."""
    IDLE = auto()
    GUIDING = auto()
    PAUSED = auto()