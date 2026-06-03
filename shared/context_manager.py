# shared/context_manager.py
"""
Context Manager - Wrapper thread-safe para SystemContext.
Versión mejorada del código original con mejor API.
"""
import logging
import threading
import time
from typing import Optional, Callable, Any, List
from contextlib import contextmanager

from .data_structures import SystemContext, CorrectionProfile, DetectedStarInfo

logger = logging.getLogger("ContexManager")

class ContextManager:
    """
    Manager thread-safe para acceso al contexto del sistema.
    Implementa patrón RAII para locks y logging de cambios.
    
    Uso:
        # Lectura
        with ctx.read() as context:
            value = context.user.cam_guide_scale
        
        # Escritura
        with ctx.write() as context:
            context.user.cam_guide_scale = 3.36
    """
    
    def __init__(self, context: Optional[SystemContext] = None):
        self.logger = logging.getLogger("ContexManager")
        self._context = context or SystemContext()
        self._lock = threading.RLock()  # Reentrant para nested access
        self._change_callbacks: List[Callable[[str, Any, Any], None]] = []
        self._last_access = time.time()
    
    # -------------------------------------------------------------------------
    # Context Managers para acceso seguro
    # -------------------------------------------------------------------------
    
    @contextmanager
    def read(self):
        """
        Context manager para lectura.
        Uso: with ctx.read() as context: ...
        """
        self._lock.acquire()
        try:
            yield self._context
        finally:
            self._last_access = time.time()
            self._lock.release()
    
    @contextmanager
    def write(self):
        """
        Context manager para escritura.
        Actualiza timestamp automáticamente.
        """
        self._lock.acquire()
        try:
            yield self._context
        finally:
            self._context.touch()
            self._last_access = time.time()
            self._lock.release()
    
    # -------------------------------------------------------------------------
    # Métodos de conveniencia (para código simple)
    # -------------------------------------------------------------------------
    
    def get_context_ref(self) -> SystemContext:
        """
        Retorna referencia directa (solo usar dentro de un lock).
        Preferir usar read()/write().
        """
        return self._context
    
    def update_user_config(self, pixel_size: Optional[float] = None, 
                            focal: Optional[float] = None) -> bool:
        """Actualiza configuración de usuario con validación.
        
        Args:
            pixel_size: Tamaño del píxel en µm (ej: 1.55 para IMX477).
                        La escala en arcsec/px se calcula automáticamente.
            focal: Distancia focal en mm.
        """
        logger = logging.getLogger("ContextManager")
        logger.debug(f"update_user_config llamado: pixel_size={pixel_size}, focal={focal}")
        
        with self.write() as ctx:
            try:
                old_pixel_size = ctx.user.pixel_size_um
                old_focal = ctx.user.focal_distance
                old_scale = ctx.user.px_scale  # valor calculado antes del update
                
                logger.debug(f"Valores antiguos: pixel_size={old_pixel_size}, focal={old_focal}, px_scale={old_scale}")
                
                ctx.user.update(pixel_size=pixel_size, focal=focal)
                
                new_scale = ctx.user.px_scale  # valor calculado después del update
                logger.debug(f"Valores nuevos: pixel_size={ctx.user.pixel_size_um}, focal={ctx.user.focal_distance}, px_scale={new_scale}")
                
                if pixel_size is not None:
                    self._notify_change('user.pixel_size_um', old_pixel_size, pixel_size)
                    self._notify_change('user.px_scale', old_scale, new_scale)
                if focal is not None:
                    self._notify_change('user.focal_distance', old_focal, focal)
                    # Si cambia la focal, también cambia la escala calculada
                    if pixel_size is None:
                        self._notify_change('user.px_scale', old_scale, new_scale)
                
                return True
            except ValueError as e:
                logger.error(f"Error en update_user_config: {e}")
                return False
    
    def update_star_selection(self, star_id: int, x: float, y: float, 
                              snr: float = 0.0) -> None:
        """Actualiza estrella seleccionada."""
        with self.write() as ctx:
            old_id = ctx.star.num_estrella
            ctx.star.num_estrella = star_id
            ctx.star.centroid_x = x
            ctx.star.centroid_y = y
            ctx.star.snr = snr
            self._notify_change('star.num_estrella', old_id, star_id)
    
    def clear_star_selection(self) -> None:
        """Limpia selección de estrella."""
        with self.write() as ctx:
            old_id = ctx.star.num_estrella
            ctx.star.clear()
            self._notify_change('star.num_estrella', old_id, 0)
    
    def update_detected_stars(self, stars: List[DetectedStarInfo]):
        """Actualiza lista de estrellas detectadas."""
        with self.write() as ctx:
            ctx.detected_stars = stars
    
    def add_correction(self, correction: CorrectionProfile) -> None:
        """Agrega corrección al histórico."""
        with self.write() as ctx:
            ctx.add_correction(correction)
    
    def set_operational_status(self, status) -> None:
        """Actualiza estado operativo (para telemetry)."""
        with self.write() as ctx:
            old = ctx.operational_status
            ctx.operational_status = status
            self._notify_change('operational_status', old, status)
    
    def set_connection_status(self, status: str) -> None:
        """Actualiza estado de conexión."""
        with self.write() as ctx:
            old = ctx.connection_status
            ctx.connection_status = status
            self._notify_change('connection_status', old, status)
    
    # -------------------------------------------------------------------------
    # Callbacks para cambios
    # -------------------------------------------------------------------------
    
    def on_change(self, callback: Callable[[str, Any, Any], None]) -> None:
        """
        Registra callback para cambios en contexto.
        Callback recibe: (field_name, old_value, new_value)
        """
        self._change_callbacks.append(callback)
    
    def _notify_change(self, field: str, old_val: Any, new_val: Any) -> None:
        """Notifica a suscriptores de cambio."""
        for cb in self._change_callbacks:
            try:
                cb(field, old_val, new_val)
            except Exception as e:
                # No dejar que callback falle afecte el sistema
                pass
    
    # -------------------------------------------------------------------------
    # Checks de estado (usados por transiciones)
    # -------------------------------------------------------------------------
    
    def has_initial_params(self) -> bool:
        with self.read() as ctx:
            return ctx.has_initial_params()
    
    def has_star_selected(self) -> bool:
        with self.read() as ctx:
            return ctx.has_star_selected()
    
    def is_calibrated(self) -> bool:
        with self.read() as ctx:
            return ctx.is_calibrated()
    
    def get_summary(self) -> dict:
        """Resumen para logs/debug."""
        with self.read() as ctx:
            return {
                'initial_params': ctx.has_initial_params(),
                'star_selected': ctx.has_star_selected(),
                'calibrated': ctx.is_calibrated(),
                'last_updated': ctx.last_updated,
                'connection': ctx.connection_status
            }
    
    def clear_all(self):
        """Reset completo del contexto."""
        with self.write() as ctx:
            ctx.clear_all()