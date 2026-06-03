# infrastructure/st4_gateway.py
"""
Gateway para comandos ST4 (movimiento de montura).
Abstrae el protocolo específico del hardware.
"""

from typing import Optional
from dataclasses import dataclass
from enum import Enum

from .tcp_client import TCPClient


class Direction(Enum):
    """Direcciones de movimiento ST4."""
    RA_PLUS = "RA+"
    RA_MINUS = "RA-"
    DEC_PLUS = "DEC+"
    DEC_MINUS = "DEC-"


@dataclass
class PulseGuide:
    """Comando de pulso de guiado."""
    direction: Direction
    duration_ms: int
    is_calibration: bool = False  # True si es pulso de calibración


class ST4Gateway:
    """
    Gateway de alto nivel para comandos ST4.
    Oculta detalles del protocolo TCP y proporciona API semántica.
    """
    
    def __init__(self, tcp_client, calibration_pulse_ms: int):
        self.tcp = tcp_client
        self.calibration_pulse_ms = calibration_pulse_ms

    
    def guide(self, direction: Direction, duration_ms: int) -> bool:
        """
        Envía comando de guiado estándar.
        
        Args:
            direction: Dirección del movimiento
            duration_ms: Duración en milisegundos
        
        Returns:
            True si el comando fue enviado exitosamente
        """
        pulse = PulseGuide(direction=direction, duration_ms=duration_ms)
        return self._send_pulse(pulse)
    
    def calibrate_pulse(self, direction: Direction) -> bool:
        """
        Envía pulso de calibración (duración fija).
        Usado durante el proceso de calibración del sistema.
        """
        pulse = PulseGuide(
            direction=direction,
            duration_ms=self.calibration_pulse_ms,
            is_calibration=True
        )
        return self._send_pulse(pulse)
    
    def stop(self) -> bool:
        """
        Comando de emergencia para detener movimiento.
        Nota: Depende de si tu hardware soporta "stop" o solo pulsos.
        """
        # Tu servidor actual no tiene comando STOP explícito
        # pero podrías enviar un pulso de 0ms o implementar lógica especial
        return True
    
    def _send_pulse(self, pulse: PulseGuide) -> bool:
        if not self.tcp.is_connected():
            return False

        self._last_command = pulse

        # Aceptar tanto Direction enum como string directo
        direction = (
            pulse.direction.value
            if isinstance(pulse.direction, Direction)
            else pulse.direction
        )

        return self.tcp.send_st4_pulse(
            direction=direction,
            duration_ms=pulse.duration_ms
        )
    
    def get_last_command(self) -> Optional[PulseGuide]:
        """Retorna último comando enviado (para logging/debug)."""
        return self._last_command