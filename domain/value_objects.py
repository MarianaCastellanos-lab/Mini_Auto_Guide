# domain/value_objects.py
"""
Value Objects - Inmutables con validación.
Representan conceptos del dominio con reglas de negocio.
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class PixelScale:
    """
    Escala de píxeles en arcosegundos por píxel.
    Valores típicos: 0.5 a 10 arcsec/px
    """
    arcsec_per_pixel: float
    
    def __post_init__(self):
        if self.arcsec_per_pixel <= 0:
            raise ValueError("Pixel scale debe ser positivo")
        if self.arcsec_per_pixel > 100:
            raise ValueError("Pixel scale parece demasiado alto (>100 arcsec/px)")
    
    def pixels_to_arcsec(self, pixels: float) -> float:
        """Convierte píxeles a arcosegundos."""
        return pixels * self.arcsec_per_pixel
    
    def arcsec_to_pixels(self, arcsec: float) -> float:
        """Convierte arcosegundos a píxeles."""
        return arcsec / self.arcsec_per_pixel
    
    def __float__(self) -> float:
        return self.arcsec_per_pixel


@dataclass(frozen=True)
class FocalLength:
    """
    Distancia focal en milímetros.
    Valores típicos: 100 a 3000 mm
    """
    mm: float
    
    def __post_init__(self):
        if self.mm <= 0:
            raise ValueError("Distancia focal debe ser positiva")
        if self.mm > 10000:
            raise ValueError("Distancia focal parece demasiado alta (>10m)")
    
    def __float__(self) -> float:
        return self.mm


@dataclass(frozen=True)
class ExposureTime:
    """
    Tiempo de exposición en segundos.
    Rango típico: 0.001 a 30 segundos
    """
    seconds: float
    
    def __post_init__(self):
        if self.seconds <= 0:
            raise ValueError("Tiempo de exposición debe ser positivo")
        if self.seconds > 60:
            raise ValueError("Exposición muy larga (>60s), verificar valor")
    
    def to_milliseconds(self) -> int:
        """Convierte a milisegundos."""
        return int(self.seconds * 1000)
    
    def __float__(self) -> float:
        return self.seconds


@dataclass(frozen=True)
class Gain:
    """
    Ganancia de la cámara (ISO o valor digital).
    Rango típico: 0 a 800
    """
    value: int
    
    def __post_init__(self):
        if self.value < 0:
            raise ValueError("Ganancia no puede ser negativa")
        if self.value > 1600:
            raise ValueError("Ganancia muy alta (>1600), posible ruido excesivo")
    
    def __int__(self) -> int:
        return self.value


@dataclass(frozen=True)
class Coordinates:
    """
    Coordenadas 2D en píxeles de imagen.
    """
    x: float
    y: float
    
    def __post_init__(self):
        if self.x < 0 or self.y < 0:
            raise ValueError("Coordenadas no pueden ser negativas")
    
    def distance_to(self, other: 'Coordinates') -> float:
        """Distancia euclidiana a otras coordenadas."""
        import math
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)
    
    def __iter__(self):
        """Permite desempaquetado: x, y = coord"""
        yield self.x
        yield self.y


@dataclass(frozen=True)
class SNR:
    """
    Signal-to-Noise Ratio de una estrella.
    Valores > 20 son buenos, > 40 excelentes.
    """
    value: float
    
    def __post_init__(self):
        if self.value < 0:
            raise ValueError("SNR no puede ser negativo")
    
    def is_good(self) -> bool:
        """Retorna True si SNR es aceptable para guiado."""
        return self.value >= 20.0
    
    def is_excellent(self) -> bool:
        """Retorna True si SNR es excelente."""
        return self.value >= 40.0
    
    def __float__(self) -> float:
        return self.value
    
    def __str__(self) -> str:
        return f"{self.value:.1f}"


@dataclass(frozen=True)
class Angle:
    """
    Ángulo en grados, normalizado a [0, 360).
    """
    degrees: float
    
    def __post_init__(self):
        object.__setattr__(self, 'degrees', self.degrees % 360)
    
    def to_radians(self) -> float:
        """Convierte a radianes."""
        import math
        return math.radians(self.degrees)
    
    def __float__(self) -> float:
        return self.degrees


@dataclass(frozen=True)
class Velocity:
    """
    Velocidad de deriva en arcosegundos por segundo.
    """
    arcsec_per_second: float
    
    def __post_init__(self):
        if self.arcsec_per_second < 0:
            raise ValueError("Velocidad no puede ser negativa (usar dirección)")
    
    def __float__(self) -> float:
        return self.arcsec_per_second


@dataclass(frozen=True)
class Aggressiveness:
    """
    Agresividad del guiado (0-100%).
    """
    percent: float
    
    def __post_init__(self):
        if not 0 <= self.percent <= 100:
            raise ValueError("Agresividad debe estar entre 0 y 100")
    
    def as_factor(self) -> float:
        """Retorna factor 0.0-1.0."""
        return self.percent / 100.0
    
    def __float__(self) -> float:
        return self.percent


@dataclass(frozen=True)
class MinMovement:
    """
    Mínimo movimiento en arcosegundos que se corrige.
    Debajo de esto se ignora el error.
    """
    arcsec: float
    
    def __post_init__(self):
        if self.arcsec < 0:
            raise ValueError("MinMo no puede ser negativo")
        if self.arcsec > 5:
            raise ValueError("MinMo muy alto (>5 arcsec), guiado será impreciso")
    
    def __float__(self) -> float:
        return self.arcsec