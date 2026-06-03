# infrastructure/connection_manager.py
"""
ConnectionManager adaptado a 2 puertos.
"""

import threading
import time
from enum import Enum, auto
from .st4_gateway import ST4Gateway
from typing import Callable, Optional
import logging
from .tcp_client import TCPClient
from config import CONFIG

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Connection_manager")

class ConnectionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    STREAMING = auto()
    ERROR = auto()


class ConnectionManager:
    """
    Gestiona conexión de 2 puertos con el servidor.
    """
    
    def __init__(self):
        self.logger = logging.getLogger("Connection_manager")

        self.tcp = TCPClient(
            host=CONFIG.NETWORK.HOST,
            port_data=CONFIG.NETWORK.PORT_DATA,
            port_cmd=CONFIG.NETWORK.PORT_CMD
        )
        
        # Crear el pipeline de imágenes
        from .image_stream import ImageStreamReceiver
        self.image_stream = ImageStreamReceiver(max_queue_size=2)
        
        # Conectar TCP al pipeline: las imágenes que llegan por TCP
        # se empujan a la cola del ImageStreamReceiver
        self.tcp.on_image_received = self.image_stream.push_image
        

        self.st4 = ST4Gateway(
            tcp_client=self.tcp, # ← CORREGIDO:
            calibration_pulse_ms=CONFIG.CALIBRATION.CALIBRATION_PULSE_MS
        )

        self._state = ConnectionState.DISCONNECTED
        self._state_callbacks = []
        
        # Conectar callbacks
        self.tcp.on_response_received = self._on_server_response
    
    def _on_server_response(self, response):
        """Procesa respuestas ST4 del servidor."""
        pass
    
    def connect(self) -> bool:
        self._set_state(ConnectionState.CONNECTING)
        
        if self.tcp.connect():
            self._set_state(ConnectionState.CONNECTED)
            # FIX: arrancar el procesador de cola del ImageStreamReceiver
            # antes de iniciar la recepción TCP, para que ningún frame
            # quede sin procesar
            self.image_stream.start()
            self.tcp.start_image_stream()
            self._set_state(ConnectionState.STREAMING)
            return True
        else:
            self._set_state(ConnectionState.ERROR)
            return False
    
    def disconnect(self):
        self.image_stream.stop()
        self.tcp.disconnect()
        self._set_state(ConnectionState.DISCONNECTED)
    
    def subscribe_to_images(self, callback: Callable):
        """
        Suscribe callback para recibir imágenes PIL.
        El callback se registra en el ImageStreamReceiver, que es quien
        consume la cola y notifica a los suscriptores.
        """
        self.image_stream.subscribe(callback)
    
    def send_camera_settings(self, exposure_us: int, gain_raw: float):
        """
        Envía configuración de cámara.
        exposure_us: microsegundos (100 - 2,000,000)
        gain_raw: valor real (1.0 - 16.0)
        """
        self.tcp.send_exposure(exposure_us)
        self.tcp.send_gain(gain_raw)
    
    def guide_pulse(self, direction: str, duration_ms: int):
        """
        Envía pulso de guiado.
        direction: 'RA+', 'RA-', 'DEC+', 'DEC-' (convertido internamente)
        """
        self.tcp.send_st4_pulse(direction, duration_ms)
    
    def _set_state(self, state: ConnectionState):
        self._state = state
        for cb in self._state_callbacks:
            cb(state)
    
    def on_state_change(self, callback):
        self._state_callbacks.append(callback)
    
    @property
    def state(self):
        return self._state

    def subscribe_star_selection_to_images(self, star_selection_service):
        """
        Conecta el servicio de selección de estrellas al stream de imágenes.
        """
        def on_image(image):
            star_selection_service.on_image_received(image)
        
        # FIX: usar self.image_stream (no self.tcp.image_stream, que no existe)
        self.image_stream.subscribe(on_image)
        logger.info("StarSelectionService suscrito al stream de imágenes")