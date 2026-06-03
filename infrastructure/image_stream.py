# infrastructure/image_stream.py
"""
Pipeline de procesamiento de imágenes con soporte para ROI futuro.
Desacopla la recepción TCP del procesamiento y la UI.
"""

import threading
import queue
from typing import Callable, Optional, Tuple
from PIL import Image
from dataclasses import dataclass


@dataclass
class ROIRegion:
    """Región de interés para futuro ROI dinámico."""
    x: int
    y: int
    width: int
    height: int


class ImageStreamReceiver:
    """
    Pipeline de imágenes que desacopla recepción de procesamiento.
    Features:
    - Cola con descarte de frames antiguos (prioriza tiempo real)
    - Soporte preparado para ROI dinámico
    - Notificación a múltiples suscriptores (UI, procesamiento, etc.)
    """
    
    def __init__(self, max_queue_size: int = 2):
        """
        Args:
            max_queue_size: 2 es ideal para video en tiempo real
                           (evita latencia por frames acumulados)
        """
        self._queue: queue.Queue[Image.Image] = queue.Queue(maxsize=max_queue_size)
        self._subscribers: list[Callable[[Image.Image], None]] = []
        self._lock = threading.Lock()
        self._running = False
        self._processor_thread: Optional[threading.Thread] = None
        
        # ROI (futuro)
        self._roi_enabled = False
        self._roi_region: Optional[ROIRegion] = None
        
        # Estadísticas
        self._frames_dropped = 0
        self._frames_processed = 0
    
    # =====================================================================
    # SUSCRIPCIÓN
    # =====================================================================
    
    def subscribe(self, callback: Callable[[Image.Image], None]):
        """
        Suscribe un callback para recibir imágenes procesadas.
        Puede llamarse múltiples veces para múltiples receptores.
        """
        with self._lock:
            self._subscribers.append(callback)
    
    def unsubscribe(self, callback: Callable[[Image.Image], None]):
        """Desuscribe un callback."""
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
    
    # =====================================================================
    # CONTROL DE FLUJO
    # =====================================================================
    
    def start(self):
        """Inicia el procesador de imágenes."""
        if self._running:
            return
        
        self._running = True
        self._processor_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._processor_thread.start()
    
    def stop(self):
        """Detiene el procesador."""
        self._running = False
        if self._processor_thread:
            self._processor_thread.join(timeout=1.0)
    
    def push_image(self, image: Image.Image):
        """
        Recibe una imagen del TCPClient.
        Si la cola está llena, descarta la más antigua (prioriza tiempo real).
        """
        try:
            # Intentar encolar sin bloquear
            self._queue.put_nowait(image)
        except queue.Full:
            # Cola llena: descartar frame antiguo y poner nuevo
            try:
                self._queue.get_nowait()
                self._frames_dropped += 1
                self._queue.put_nowait(image)
            except queue.Empty:
                pass  # Race condition, ignorar
    
    # =====================================================================
    # ROI (Preparado para futuro)
    # =====================================================================
    
    def set_roi(self, region: ROIRegion):
        """
        Activa ROI dinámico.
        Cuando implementes esto en el servidor, aquí filtrarías
        o enviarías comando al servidor para cambiar región.
        """
        self._roi_enabled = True
        self._roi_region = region
        # TODO: Enviar comando al servidor para cambiar ROI
    
    def clear_roi(self):
        """Desactiva ROI, vuelve a full frame."""
        self._roi_enabled = False
        self._roi_region = None
    
    def _apply_roi(self, image: Image.Image) -> Image.Image:
        """Aplica ROI a imagen si está activo."""
        if not self._roi_enabled or not self._roi_region:
            return image
        
        roi = self._roi_region
        # Verificar límites
        width, height = image.size
        x = max(0, min(roi.x, width - roi.width))
        y = max(0, min(roi.y, height - roi.height))
        
        return image.crop((x, y, x + roi.width, y + roi.height))
    
    # =====================================================================
    # PROCESAMIENTO
    # =====================================================================
    
    def _process_loop(self):
        """Loop principal de procesamiento."""
        while self._running:
            try:
                # Esperar imagen con timeout para poder verificar _running
                image = self._queue.get(timeout=0.1)
                
                # Aplicar ROI si aplica
                if self._roi_enabled:
                    image = self._apply_roi(image)
                
                # Notificar a suscriptores
                self._notify_subscribers(image)
                self._frames_processed += 1
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error procesando imagen: {e}")
    
    def _notify_subscribers(self, image: Image.Image):
        """Notifica a todos los suscriptores."""
        with self._lock:
            subscribers = self._subscribers.copy()
        
        for callback in subscribers:
            try:
                callback(image)
            except Exception as e:
                print(f"Error en subscriber: {e}")
    
    def get_stats(self) -> dict:
        """Retorna estadísticas del pipeline."""
        return {
            'frames_processed': self._frames_processed,
            'frames_dropped': self._frames_dropped,
            'queue_size': self._queue.qsize(),
            'roi_enabled': self._roi_enabled
        }