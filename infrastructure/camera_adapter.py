# infrastructure/camera_adapter.py
"""
CameraAdapter — adapta ImageStreamReceiver a la interfaz capture_single_frame().

CalibrationService y GuidingService necesitan una llamada bloqueante que
entregue un frame nuevo cada vez que se invoca. ImageStreamReceiver es
asíncrono (notifica por callbacks). Este adaptador hace el puente:

  - Se suscribe una sola vez al ImageStreamReceiver en el constructor.
  - Cada llamada a capture_single_frame() limpia un threading.Event,
    espera a que el callback lo active con el próximo frame, y lo retorna.
  - "Próximo frame" significa el primer frame que llegue DESPUÉS de que
    se limpió el Event → garantiza que no se reutiliza el frame anterior.
"""

import threading
import logging
from typing import Optional
from PIL import Image

from .image_stream import ImageStreamReceiver

logger = logging.getLogger("CameraAdapter")


class CameraAdapter:
    """
    Adaptador que expone capture_single_frame() sobre un ImageStreamReceiver.

    Uso:
        adapter = CameraAdapter(connection_manager.image_stream)
        frame = adapter.capture_single_frame()   # bloqueante, retorna PIL Image
    """

    def __init__(self, image_stream: ImageStreamReceiver,
                 timeout_seconds: float = 10.0):
        """
        Args:
            image_stream:    ImageStreamReceiver al que suscribirse.
            timeout_seconds: Tiempo máximo de espera por frame antes de
                             retornar None. 10s es generoso para cualquier
                             exposición normal (≤ 2s).
        """
        self._stream = image_stream
        self._timeout = timeout_seconds

        self._event = threading.Event()
        self._latest_frame: Optional[Image.Image] = None
        self._lock = threading.Lock()

        # Suscribirse una sola vez — el callback se activa en cada frame nuevo
        self._stream.subscribe(self._on_frame)
        logger.debug("CameraAdapter suscrito al ImageStreamReceiver")

    def _on_frame(self, image: Image.Image):
        """Callback llamado por ImageStreamReceiver con cada frame nuevo."""
        with self._lock:
            self._latest_frame = image
        self._event.set()   # Desbloquea capture_single_frame() si está esperando

    def capture_single_frame(self) -> Optional[Image.Image]:
        """
        Bloquea hasta recibir el PRÓXIMO frame del stream y lo retorna.

        Limpia el Event antes de esperar para asegurar que el frame
        retornado fue capturado después de esta llamada (no es un frame
        del buffer anterior).

        Returns:
            PIL Image del frame recibido, o None si se agotó el timeout.
        """
        self._event.clear()   # Descartar cualquier señal previa

        arrived = self._event.wait(timeout=self._timeout)

        if not arrived:
            logger.warning(
                f"capture_single_frame(): timeout tras {self._timeout}s — "
                "¿está el stream activo?"
            )
            return None

        with self._lock:
            frame = self._latest_frame

        return frame