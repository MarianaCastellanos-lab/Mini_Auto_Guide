# domain/entities.py
"""
Entidades del dominio - Tienen identidad y ciclo de vida.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

from .value_objects import (
    Coordinates, SNR, PixelScale, Angle, Velocity, 
    ExposureTime, Gain, Aggressiveness, MinMovement,
    FocalLength  # <-- AÑADIR ESTA LÍNEA
)
from .enums import Direction, CalibrationStatus


@dataclass
class Star:
    """
    Estrella detectada en el campo de visión.
    Entidad porque tiene identidad (id) y cambia con el tiempo (SNR varía).
    """
    id: int
    coordinates: Coordinates
    snr: SNR
    
    # Actualizable
    is_selected: bool = False
    last_updated: datetime = field(default_factory=datetime.now)
    
    def update_snr(self, new_snr: SNR):
        """Actualiza SNR (varía con condiciones atmosféricas)."""
        self.snr = new_snr
        self.last_updated = datetime.now()
    
    def select(self):
        """Marca como estrella de guiado."""
        self.is_selected = True
    
    def deselect(self):
        self.is_selected = False


@dataclass
class CalibrationData:
    """
    Resultados de calibración del sistema.
    Inmutable una vez completada.
    """
    px_scale: PixelScale
    camera_angle: Angle
    vel_ra: Velocity
    vel_dec: Velocity
    ra_steps: int
    ort_error: Angle
    
    timestamp: datetime = field(default_factory=datetime.now)
    status: CalibrationStatus = CalibrationStatus.IDLE
    
    def is_valid(self) -> bool:
        """Verifica si los datos parecen válidos."""
        return (
            self.status == CalibrationStatus.COMPLETED and
            0 < float(self.camera_angle) < 90 and
            float(self.vel_ra) > 0 and
            float(self.vel_dec) > 0 and
            0 < float(self.ort_error) < 5  # Error ortogonalidad < 5°
        )


@dataclass
class GuidingError:
    """
    Error de guiado en un instante específico.
    Value object temporal, no entidad.
    """
    ra_arcsec: float
    dec_arcsec: float
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def total_arcsec(self) -> float:
        """Error total (magnitud)."""
        import math
        return math.sqrt(self.ra_arcsec**2 + self.dec_arcsec**2)
    
    @property
    def ra_pixels(self, px_scale: float = 3.36) -> float:
        """Error RA en píxeles."""
        return self.ra_arcsec / px_scale
    
    @property
    def dec_pixels(self, px_scale: float = 3.36) -> float:
        """Error DEC en píxeles."""
        return self.dec_arcsec / px_scale


@dataclass
class Correction:
    """
    Corrección enviada a la montura.
    """
    direction: Direction
    duration_ms: int
    timestamp: datetime = field(default_factory=datetime.now)
    
    def __post_init__(self):
        if self.duration_ms < 0:
            raise ValueError("Duración no puede ser negativa")
        if self.duration_ms > 5000:
            raise ValueError("Duración muy larga (>5s)")


@dataclass
class UserConfiguration:
    """
    Configuración ingresada por el usuario.
    """
    # Parámetros obligatorios iniciales
    px_scale: Optional[PixelScale] = None
    focal_length: Optional[FocalLength] = None
    
    # Parámetros de cámara
    exposure: ExposureTime = field(default_factory=lambda: ExposureTime(1.0))
    gain: Gain = field(default_factory=lambda: Gain(100))
    
    # Parámetros de guiado
    aggressiveness: Aggressiveness = field(default_factory=lambda: Aggressiveness(70))
    min_mo: MinMovement = field(default_factory=lambda: MinMovement(0.2))
    
    # Parámetros de gráfico
    graph_x_scale: int = 100  # Muestras a mostrar
    graph_y_scale: float = 5.0  # Arcsec ±
    
    def is_complete_for_guiding(self) -> bool:
        """Verifica si tenemos todos los parámetros necesarios."""
        return self.px_scale is not None and self.focal_length is not None


@dataclass
class SystemTelemetry:
    """
    Telemetría del sistema en tiempo real.
    """
    # Estado
    connection_status: str = "disconnected"
    guiding_status: str = "idle"
    state_number: int = 1
    
    # Frames
    fps: float = 0.0
    frames_dropped: int = 0
    
    # Errores
    last_error_ra: float = 0.0
    last_error_dec: float = 0.0
    rms_error: float = 0.0
    
    # Correcciones
    total_corrections: int = 0
    last_correction_duration_ms: int = 0
    
    timestamp: datetime = field(default_factory=datetime.now)