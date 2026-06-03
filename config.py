# config.py
"""
Configuración global del sistema de autoguiado.
ADAPTADO a server_st4_v1.py y client_st4_v1.py actuales.
"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NetworkConfig:
    """
    Configuración de red - 2 PUERTOS como en tu implementación actual.
    Puerto 8000: Stream de imágenes (con header binario)
    Puerto 8001: Comandos JSON (línea por línea)
    """
    HOST: str = "192.168.7.2"  # IP de la Raspberry Pi via RNDIS/CDC

    # Puerto de streaming de imágenes (header 12 bytes + JPEG)
    PORT_DATA: int = 8000

    # Puerto de comandos JSON (separado)
    PORT_CMD: int = 8001

    TIMEOUT_SECONDS: float = 5.0
    RECONNECT_DELAY_SECONDS: float = 3.0
    MAX_RECONNECT_ATTEMPTS: int = 5

    # Buffers TCP
    RCV_BUFFER_SIZE: int = 256 * 1024  # 256KB
    SND_BUFFER_SIZE: int = 64 * 1024   # 64KB


@dataclass(frozen=True)
class CameraConfig:
    """Configuración de la cámara IMX477."""

    # Resolución real del servidor: 1014×760 (binning 2×2)
    WIDTH: int = 1014
    HEIGHT: int = 760

    STREAM_WIDTH: int = 1014
    STREAM_HEIGHT: int = 760

    # Rango de exposición en MICROSEGUNDOS
    MIN_EXPOSURE_US: int = 100           # 100 µs = 0.1 ms
    MAX_EXPOSURE_US: int = 2_000_000     # 2,000,000 µs = 2s
    DEFAULT_EXPOSURE_US: int = 1_000_000 # 1,000,000 µs = 1s

    # Rango de ganancia
    MIN_GAIN_RAW: float = 1.0
    MAX_GAIN_RAW: float = 16.0
    DEFAULT_GAIN_RAW: float = 1.0

    # Factor de conversión UI→protocolo (valor real × 10)
    GAIN_MULTIPLIER: int = 10


@dataclass(frozen=True)
class ST4Config:
    """Configuración ST4 — direcciones en español como espera el servidor."""

    # Mapeo de direcciones internas a direcciones del servidor
    DIRECTION_MAP: dict = field(default_factory=lambda: {
        'RA+':  'este',
        'RA-':  'oeste',
        'DEC+': 'norte',
        'DEC-': 'sur',
    })

    # Inverso: del servidor a interno
    SERVER_TO_INTERNAL: dict = field(default_factory=lambda: {
        'norte': 'DEC+',
        'sur':   'DEC-',
        'este':  'RA+',
        'oeste': 'RA-',
    })

    MIN_DURATION_MS: int = 50
    MAX_DURATION_MS: int = 5000
    DEFAULT_DURATION_MS: int = 500


@dataclass(frozen=True)
class CalibrationConfig:
    """
    Parámetros del algoritmo de calibración.
    Valores validados en el simulador PHD2 con montura ZEQ25.
    """
    # Duración del pulso de calibración en milisegundos.
    # 1500ms da desplazamientos medibles sin mover la estrella fuera del frame.
    CALIBRATION_PULSE_MS: int = 2000

    # Segundos de espera entre el pulso RA y la captura pre-DEC.
    # Permite que la montura absorba el backlash mecánico antes de medir DEC.
    BACKLASH_WAIT_SECONDS: float = 5.0

    # Frames consecutivos estables requeridos para aceptar un centroide.
    # 3 es suficiente para hardware real con seeing normal.
    CENTROID_SETTLE_FRAMES: int = 2

    # Variación máxima en px entre frames para considerar centroide "estable".
    CENTROID_STABLE_THRESHOLD_PX: float = 1


    # Margen adicional a exposure_s en post_pulse_wait e inter_frame_wait.
    # 0.0 para hardware real: capture_single_frame() ya es bloqueante y
    # garantiza frames nuevos sin espera adicional.
    FRAME_SETTLE_MARGIN_SECONDS: float = 0.0


@dataclass(frozen=True)
class GuidingConfig:
    """
    Parámetros del algoritmo de guiado.
    Valores validados en el simulador PHD2.
    """
    # Frecuencia del loop de guiado.
    # 2.0 Hz en el simulador (limitado por PHD2 a ~1 FPS).
    # Hardware real con IMX477: puede subirse hasta donde la exposición lo permita.
    # Con exposición de 1s, el loop efectivo es ~1 Hz. 2.0 es un buen valor inicial.
    GUIDE_FREQUENCY_HZ: float = 2.0

    # Tamaño de ventana para cálculo de RMS (número de muestras).
    # 50 muestras a 2 Hz = ~25 segundos de historial.
    RMS_WINDOW_SAMPLES: int = 50

    # Fracción máxima de la exposición que puede durar un pulso de guiado.
    # 0.9 = el pulso no puede durar más del 90% del tiempo de exposición.
    # Evita mover la montura mientras la cámara está integrando.
    MAX_PULSE_EXPOSURE_RATIO: float = 0.9

    # Límite absoluto de duración de pulso de guiado en milisegundos.
    # 2000ms = 2s. Protege contra correcciones excesivas por errores de detección.
    MAX_GUIDE_PULSE_MS: int = 2000


@dataclass(frozen=True)
class UIConfig:
    """Configuración de la interfaz gráfica."""
    WINDOW_WIDTH: int = 1100
    WINDOW_HEIGHT: int = 750
    WINDOW_TITLE: str = "AUTO GUIADO MINI - IMX477 + ZEQ25"

    GRAPH_UPDATE_HZ: float = 20.0
    MAX_GRAPH_POINTS: int = 500

    COLOR_DISCONNECTED: str = "#444444"
    COLOR_CONNECTING: str = "#664411"
    COLOR_CONNECTED: str = "#116611"
    COLOR_ERROR: str = "#661111"
    COLOR_GUIDING: str = "#008800"


@dataclass(frozen=True)
class AppConfig:
    """Configuración global."""
    SIMULATION_MODE: bool = False

    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))

    NETWORK:     NetworkConfig     = field(default_factory=NetworkConfig)
    CAMERA:      CameraConfig      = field(default_factory=CameraConfig)
    ST4:         ST4Config         = field(default_factory=ST4Config)
    CALIBRATION: CalibrationConfig = field(default_factory=CalibrationConfig)
    GUIDING:     GuidingConfig     = field(default_factory=GuidingConfig)
    UI:          UIConfig          = field(default_factory=UIConfig)


CONFIG = AppConfig()