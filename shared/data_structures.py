# shared/data_structures.py
"""
Estructuras de datos compartidas - Contenedores simples.
Estos son los datos que viajan entre capas.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime


@dataclass
class UserData:
    """Datos de configuración del usuario."""
    # tamaño del píxel en µm (ingresado por el usuario)
    pixel_size_um: float = 0.0      # µm (ej: 1.55 para IMX477)
    focal_distance: float = 0.0     # mm
    graph_x_scale: int = 100
    graph_y_scale: float = 5.0
    aggressiveness: float = 70.0
    min_mo: float = 0.2
    
    @property
    def px_scale(self) -> float:
        """Escala de píxeles en arcsec/px, calculada automáticamente.
        
        Fórmula: (tamaño_píxel_µm / distancia_focal_mm) * 206.265
        Ejemplo IMX477 (1.55µm) + 8mm focal: (1.55/8)*206.265 ≈ 40 arcsec/px
        """
        if self.focal_distance <= 0 or self.pixel_size_um <= 0:
            return 0.0
        return (self.pixel_size_um / self.focal_distance) * 206.265
    
    def update(self, pixel_size: Optional[float] = None, focal: Optional[float] = None):
        if pixel_size is not None:
            self.pixel_size_um = pixel_size
        if focal is not None:
            self.focal_distance = focal
    
    def is_valid(self) -> bool:
        return self.pixel_size_um > 0 and self.focal_distance > 0


@dataclass
class CameraData:
    """Estado de la cámara."""
    expo_time: float = 1.0    # segundos
    gain: int = 100
    
    def update(self, exposure: Optional[float] = None, gain: Optional[int] = None):
        if exposure is not None:
            self.expo_time = exposure
        if gain is not None:
            self.gain = gain


@dataclass
class StarData:
    """Datos de la estrella seleccionada."""
    num_estrella: int = 0
    centroid_x: float = 0.0
    centroid_y: float = 0.0
    snr: float = 0.0
    
    def clear(self):
        self.num_estrella = 0
        self.centroid_x = 0.0
        self.centroid_y = 0.0
        self.snr = 0.0
    
    def is_valid(self) -> bool:
        return self.num_estrella > 0


@dataclass
class CalibrationProfile:
    """Resultados de calibración."""
    px_scale: float = 0.0
    camera_angle: float = 0.0
    vel_ra: float = 0.0
    vel_dec: float = 0.0
    ra_steps: int = 0
    ort_error: float = 0.0
    
    def is_valid(self) -> bool:
        return self.px_scale > 0 and self.vel_ra > 0 and self.vel_dec > 0


@dataclass
class CorrectionProfile:
    """Errores de guiado actuales."""
    ra: float = 0.0           # arcsec
    dec: float = 0.0          # arcsec
    total: float = 0.0        # arcsec
    ra_oscillations: int = 0
    
    def update(self, ra: float, dec: float):
        import math
        self.ra = ra
        self.dec = dec
        self.total = math.sqrt(ra**2 + dec**2)


@dataclass
class Flags:
    """Banderas de control de flujo."""
    loop_button_clicked: bool = False
    calibrate_button_clicked: bool = False
    start_button_clicked: bool = False
    stop_button_clicked: bool = False
    
    def reset_all(self):
        self.loop_button_clicked = False
        self.calibrate_button_clicked = False
        self.start_button_clicked = False
        self.stop_button_clicked = False


@dataclass
class DetectedStarInfo:
    """Información de estrella detectada (para lista en Panel Manager)."""
    id: int
    snr: float


@dataclass 
class SystemContext:
    """
    Contexto global del sistema - Contenedor de todos los datos compartidos.
    Esta es la estructura que protege ContextManager.
    """
    # Datos de usuario
    user: UserData = field(default_factory=UserData)
    
    # Datos de cámara
    camera: CameraData = field(default_factory=CameraData)
    
    # Estrella seleccionada
    star: StarData = field(default_factory=StarData)
    
    # Calibración
    calibration: CalibrationProfile = field(default_factory=CalibrationProfile)
    
    # Errores de guiado
    errors: CorrectionProfile = field(default_factory=CorrectionProfile)
    
    # Banderas de control
    flags: Flags = field(default_factory=Flags)
    
    # Estado de conexión
    connection_status: str = "disconnected"
    
    # Estrellas detectadas (lista para Panel Manager)
    detected_stars: List[DetectedStarInfo] = field(default_factory=list)
    
    # Timestamp de última actualización
    last_updated: datetime = field(default_factory=datetime.now)
    
    # Estado operativo (para telemetry)
    operational_status: Any = None
    
    def touch(self):
        """Actualiza timestamp."""
        self.last_updated = datetime.now()
    
    def has_initial_params(self) -> bool:
        """Verifica si hay parámetros iniciales válidos."""
        return self.user.is_valid()
    
    def has_star_selected(self) -> bool:
        """Verifica si hay estrella seleccionada."""
        return self.star.is_valid()
    
    def is_calibrated(self) -> bool:
        """Verifica si se completó calibración."""
        return self.calibration.is_valid()
    
    def add_correction(self, correction: CorrectionProfile):
        """Agrega corrección al histórico."""
        # Aquí podría guardar historial
        self.errors = correction
        self.touch()
    
    def clear_all(self):
        """Limpia todo el contexto (reset)."""
        self.user = UserData()
        self.camera = CameraData()
        self.star = StarData()
        self.calibration = CalibrationProfile()
        self.errors = CorrectionProfile()
        self.flags.reset_all()
        self.detected_stars = []
        self.touch()