# shared/event_bus.py
"""
Event Bus - Sistema de publicación/suscripción thread-safe.
Desacopla componentes permitiendo comunicación indirecta.
"""

import threading
import queue
from typing import Callable, Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum, auto
import logging

logger = logging.getLogger("EventBus")


class EventType(Enum):
    """Tipos de eventos del sistema."""
    # Estado
    STATE_CHANGED = "state_changed"
    
    # Conexión
    CONNECTION_STATUS = "connection_status"
    
    # Estrellas
    STARS_DETECTED = "stars_detected"
    STARS_UPDATED = "stars_updated"
    STAR_SELECTED = "star_selected"
    
    # Calibración
    CALIBRATION_STARTED = "calibration_started"
    CALIBRATION_PROGRESS = "calibration_progress"
    CALIBRATION_COMPLETE = "calibration_complete"
    
    # Guiado
    GUIDING_STARTED = "guiding_started"
    GUIDING_STOPPED = "guiding_stopped"
    GUIDING_ERROR = "guiding_error"
    GUIDING_STATS = "guiding_stats"
    CORRECTION_SENT = "correction_sent"
    
    # UI
    BUTTON_CLICKED = "button_clicked"
    PARAMETER_CHANGED = "parameter_changed"
    
    # Errores
    ERROR_OCCURRED = "error_occurred"


@dataclass
class Event:
    """Evento del sistema."""
    type: EventType
    data: Dict[str, Any] = None
    timestamp: float = None
    
    def __post_init__(self):
        if self.data is None:
            self.data = {}
        if self.timestamp is None:
            import time
            self.timestamp = time.time()


class EventBus:
    """
    Bus de eventos thread-safe con procesamiento asíncrono.
    Permite desacoplar componentes (UI, lógica, hardware).
    """
    
    def __init__(self, max_queue_size: int = 1000):
        self._subscribers: Dict[EventType, List[Callable]] = {
            event_type: [] for event_type in EventType
        }
        self._lock = threading.RLock()
        
        # Cola para procesamiento asíncrono
        self._queue: queue.Queue[Event] = queue.Queue(maxsize=max_queue_size)
        self._running = False
        self._processor_thread: Optional[threading.Thread] = None
        
        # Callbacks globales (reciben todos los eventos)
        self._global_subscribers: List[Callable] = []
    
    def start(self):
        """Inicia procesamiento de eventos."""
        if self._running:
            return
        
        self._running = True
        self._processor_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._processor_thread.start()
        logger.info("EventBus iniciado")
    
    def stop(self):
        """Detiene procesamiento."""
        self._running = False
        if self._processor_thread:
            self._processor_thread.join(timeout=1.0)
    
    # =====================================================================
    # SUSCRIPCIÓN
    # =====================================================================
    
    def subscribe(self, event_type: EventType, callback: Callable[[Event], None]):
        """
        Suscribe callback a tipo específico de evento.
        
        Args:
            event_type: Tipo de evento a escuchar
            callback: Función que recibe Event
        """
        with self._lock:
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)
                logger.debug(f"Suscriptor añadido a {event_type.value}")
    
    def unsubscribe(self, event_type: EventType, callback: Callable[[Event], None]):
        """Desuscribe callback."""
        with self._lock:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)
    
    def subscribe_all(self, callback: Callable[[Event], None]):
        """
        Suscribe callback a TODOS los eventos (para logging/debug).
        """
        with self._lock:
            if callback not in self._global_subscribers:
                self._global_subscribers.append(callback)
    
    # =====================================================================
    # PUBLICACIÓN
    # =====================================================================
    
    def publish(self, event_type: EventType, **kwargs):
        """
        Publica evento de forma asíncrona.
        
        Args:
            event_type: Tipo de evento
            **kwargs: Datos del evento
        """
        event = Event(type=event_type, data=kwargs)
        
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            logger.warning(f"Cola de eventos llena, descartando {event_type.value}")
    
    # Método conveniente para compatibilidad con código anterior
    def publish_legacy(self, event_name: str, **kwargs):
        """
        Publica usando nombre string (para compatibilidad).
        Mapea strings a EventType si existe.
        """
        try:
            event_type = EventType(event_name)
            self.publish(event_type, **kwargs)
        except ValueError:
            # Evento custom, crear genérico
            logger.debug(f"Evento custom: {event_name}")
            # Procesar inmediatamente para eventos legacy
            self._notify_subscribers_legacy(event_name, kwargs)
    
    def _notify_subscribers_legacy(self, event_name: str, data: dict):
        """Notifica suscriptores legacy (strings)."""
        # Buscar en global subscribers
        with self._lock:
            globals = self._global_subscribers.copy()
        
        for callback in globals:
            try:
                callback(type=event_name, **data)
            except Exception as e:
                logger.error(f"Error en callback legacy: {e}")
    
    def _process_loop(self):
        """Loop de procesamiento de eventos."""
        while self._running:
            try:
                event = self._queue.get(timeout=0.1)
                self._dispatch(event)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error procesando evento: {e}")
    
    def _dispatch(self, event: Event):
        """Envía evento a suscriptores."""
        with self._lock:
            callbacks = self._subscribers[event.type].copy()
            globals = self._global_subscribers.copy()
        
        # Notificar suscriptores específicos
        for callback in callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error en callback para {event.type.value}: {e}")
        
        # Notificar globales
        for callback in globals:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error en callback global: {e}")
    
    def get_stats(self) -> dict:
        """Estadísticas del bus."""
        return {
            'queue_size': self._queue.qsize(),
            'subscribers': {
                et.value: len(subs) 
                for et, subs in self._subscribers.items()
            },
            'global_subscribers': len(self._global_subscribers)
        }