# infrastructure/protocol.py
"""
Protocolo de comunicación compatible con server_st4_v1.py actual.
"""

import json
import struct
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum

from config import CONFIG


class ServerCommandType(Enum):
    """Tipos de comandos que entiende el servidor."""
    SET_EXPOSURE = "set_exposure"
    SET_GAIN = "set_gain"
    ST4_PULSE = "st4_pulse"


@dataclass
class CameraSettings:
    """Configuración de cámara para servidor."""
    exposure_us: int  # Microsegundos (100 - 2,000,000)
    gain_raw: float   # Valor real (1.0 - 16.0)
    
    def to_protocol_dict(self) -> dict:
        """Convierte a formato del protocolo del servidor."""
        return {
            "action": "set_exposure",
            "value": self.exposure_us
        }
    
    def to_gain_dict(self) -> dict:
        """Comando separado para ganancia."""
        return {
            "action": "set_gain",
            "value": round(self.gain_raw * CONFIG.CAMERA.GAIN_MULTIPLIER)
        }


@dataclass
class ST4Command:
    """Comando ST4 compatible con servidor."""
    direction: str  # 'norte', 'sur', 'este', 'oeste' (español)
    duration_ms: int
    
    def to_protocol_dict(self) -> dict:
        """Formato exacto que espera server_st4_v1.py."""
        return {
            "action": "st4_pulse",
            "direction": self.direction,
            "duration_ms": self.duration_ms
        }


@dataclass
class ServerResponse:
    """Respuesta del servidor a comandos."""
    status: str  # 'ok', 'rejected', 'error', 'queued'
    direction: Optional[str] = None
    axis: Optional[str] = None
    actual_ms: Optional[int] = None
    message: Optional[str] = None
    
    @classmethod
    def from_json_line(cls, json_str: str) -> "ServerResponse":
        """Parsea línea JSON del servidor."""
        try:
            data = json.loads(json_str)
            return cls(
                status=data.get("status", "unknown"),
                direction=data.get("direction"),
                axis=data.get("axis"),
                actual_ms=data.get("actual_ms"),
                message=data.get("message")
            )
        except json.JSONDecodeError:
            return cls(status="parse_error", message="Invalid JSON")


class ProtocolEncoder:
    """Codifica comandos al formato exacto del servidor."""
    
    @staticmethod
    def encode_command(cmd_dict: dict) -> bytes:
        """
        Codifica comando con terminador \n como requiere el servidor.
        """
        return (json.dumps(cmd_dict) + '\n').encode('utf-8')
    
    @staticmethod
    def decode_image_header(data: bytes) -> Optional[tuple]:
        """
        Decodifica header de 12 bytes: !dI (timestamp double + size int)
        Retorna (timestamp, size) o None si datos insuficientes.
        """
        if len(data) < 12:
            return None
        return struct.unpack('!dI', data[:12])
    
    @staticmethod
    def create_exposure_command(exposure_us: int) -> bytes:
        """Comando de exposición en microsegundos."""
        cmd = {
            "action": "set_exposure",
            "value": int(exposure_us)
        }
        return ProtocolEncoder.encode_command(cmd)
    
    @staticmethod
    def create_gain_command(gain_raw: float) -> bytes:
        """Comando de ganancia (conversión ×10)."""
        cmd = {
            "action": "set_gain",
            "value": round(gain_raw * CONFIG.CAMERA.GAIN_MULTIPLIER)
        }
        return ProtocolEncoder.encode_command(cmd)
    
    @staticmethod
    def create_st4_command(direction: str, duration_ms: int) -> bytes:
        """
        Comando ST4 con dirección en español.
        direction: 'norte', 'sur', 'este', 'oeste'
        """
        cmd = {
            "action": "st4_pulse",
            "direction": direction,
            "duration_ms": int(duration_ms)
        }
        return ProtocolEncoder.encode_command(cmd)