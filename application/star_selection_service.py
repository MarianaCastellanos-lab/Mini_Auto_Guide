# application/star_selection_service.py
"""
Servicio de selección de estrella - Versión Real.
Integra procesamiento de imagen con detección de centroides.
"""

import threading
import time
from typing import List, Optional
from dataclasses import dataclass
import logging

from shared.event_bus import EventType, EventBus
from shared.context_manager import ContextManager
from application.image_processor import ImageProcessor, StarDetection
from PIL import Image

logger = logging.getLogger("StarSelectionService")


@dataclass
class DetectedStar:
    """Representa una estrella detectada (formato legacy compatible)."""
    id: int
    x: float
    y: float
    snr: float


class StarSelectionService:
    """
    Servicio de detección y selección de estrellas.
    Versión REAL: Procesa imágenes del stream de la cámara.
    """
    
    def __init__(self, context_manager: ContextManager, event_bus: EventBus):
        self.ctx = context_manager
        self.event_bus = event_bus

        # Parámetros explícitos validados en simulador PHD2
        # sigma_k=3.0, min_pixels=3, min_snr=3.0 funcionan bien para
        # frames estelares con fondo uniforme (IMX477 + JPEG 92%)
        self.image_processor = ImageProcessor(
            sigma_k=3.0,
            min_pixels=3,
            gaussian_sigma=1.0,
            snr_aperture=5,
            min_snr=3.0
        )
        
        self._detected_stars: List[StarDetection] = []
        self._detection_active = False
        self._thread: Optional[threading.Thread] = None
        
        self._last_image: Optional[Image.Image] = None
        self._image_lock = threading.Lock()
        self._processing = False
        
        logger.info("StarSelectionService inicializado")
    
    def start_detection(self):
        """
        Inicia detección de estrellas.
        Se suscribe al stream de imágenes y procesa cada frame.
        """
        if self._detection_active:
            return
        
        self._detection_active = True
        self._thread = threading.Thread(
            target=self._detection_loop, daemon=True,
            name="StarDetection"
        )
        self._thread.start()
        logger.info("Detección de estrellas iniciada")
    
    def stop_detection(self):
        """Detiene la detección."""
        self._detection_active = False
        logger.info("Detección de estrellas detenida")
    
    def on_image_received(self, image: Image.Image):
        """
        Callback para recibir imágenes del ImageStreamReceiver.
        Thread-safe: almacena la última imagen disponible.
        """
        with self._image_lock:
            self._last_image = image
    
    def _detection_loop(self):
        """
        Loop de detección.
        Procesa la última imagen disponible periódicamente.
        """
        while self._detection_active:
            image = None
            
            with self._image_lock:
                if self._last_image is not None:
                    image = self._last_image.copy()
            
            if image is not None and not self._processing:
                self._processing = True
                try:
                    self._process_frame(image)
                except Exception as e:
                    logger.error(f"Error procesando frame: {e}")
                finally:
                    self._processing = False
            
            # Procesar cada 500ms (2 FPS es suficiente para selección)
            time.sleep(0.5)
    
    def _process_frame(self, image: Image.Image):
        """Procesa un frame y actualiza detecciones."""
        detections = self.image_processor.process(image)
        
        if not detections:
            logger.debug("No se detectaron estrellas en el frame")
            return
        
        self._detected_stars = detections
        
        # Convertir a formato legacy para contexto
        legacy_stars = [
            DetectedStar(id=s.id, x=s.x, y=s.y, snr=s.snr)
            for s in detections
        ]
        
        with self.ctx.write() as ctx:
            ctx.detected_stars = legacy_stars
        
        # Publicar siempre que haya detecciones válidas
        self.event_bus.publish(
            EventType.STARS_DETECTED,
            count=len(detections),
            stars=self._get_stars_for_display(),
            top_5=self._get_top_5_details()
        )
        
        logger.debug(f"Detectadas {len(detections)} estrellas")
    
    def _get_stars_for_display(self) -> List[dict]:
        """
        Formatea estrellas para mostrar en Panel Manager.
        Formato: [{"id": 1, "snr": 32.5}, ...]
        """
        return [
            {"id": star.id, "snr": round(star.snr, 1)}
            for star in self._detected_stars
        ]
    
    def _get_top_5_details(self) -> List[dict]:
        """Retorna detalles completos de top 5 estrellas."""
        return [
            {
                "id": star.id,
                "x": round(star.x, 1),
                "y": round(star.y, 1),
                "snr": round(star.snr, 1)
            }
            for star in self._detected_stars[:5]
        ]
    
    def select_star(self, star_id: int) -> bool:
        """
        Selecciona una estrella específica por ID.
        Guarda en contexto y notifica.
        """
        star = next((s for s in self._detected_stars if s.id == star_id), None)
        if not star:
            logger.warning(f"Estrella {star_id} no encontrada")
            return False
        
        with self.ctx.write() as ctx:
            ctx.star.num_estrella = star.id
            ctx.star.centroid_x = star.x
            ctx.star.centroid_y = star.y
            ctx.star.snr = star.snr
        
        self.event_bus.publish(
            EventType.STAR_SELECTED,
            star_id=star.id,
            x=star.x,
            y=star.y,
            snr=star.snr
        )
        
        logger.info(f"Estrella {star_id} seleccionada (SNR: {star.snr:.1f})")
        return True
    
    def get_detected_stars(self) -> List[dict]:
        """Retorna lista de estrellas para mostrar en UI."""
        return self._get_stars_for_display()
    
    def get_annotated_image(self, image: Image.Image) -> Image.Image:
        """
        Retorna imagen con recuadros dibujados.
        Útil para visualización en UI.
        """
        return self.image_processor.draw_detections(image, self._detected_stars)
    
    def reset(self):
        """Limpia detección."""
        self._detected_stars = []
        self._detection_active = False
        with self.ctx.write() as ctx:
            ctx.detected_stars = []