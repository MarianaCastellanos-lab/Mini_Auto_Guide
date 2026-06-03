# infrastructure/tcp_client.py
"""
Cliente TCP de 2 puertos compatible con server_st4_v1.py.
Puerto 8000: Imágenes con header binario (12 bytes)
Puerto 8001: Comandos JSON línea por línea
"""

import socket
import struct
import json
import threading
import queue
import logging
from typing import Optional, Callable, Tuple
from io import BytesIO
from PIL import Image

from .protocol import ProtocolEncoder, ServerResponse, ServerCommandType
from config import CONFIG

logger = logging.getLogger("TCPClient")


class TCPClientError(Exception):
    pass


class TCPClient:
    """
    Cliente TCP de 2 puertos para server_st4_v1.py.
    Replica la lógica de tu NetworkThread actual.
    """
    
    HEADER_SIZE = 12  # !dI = 8 bytes double + 4 bytes int
    
    def __init__(self, host: str = None, port_data: int = None, port_cmd: int = None):
        self.logger = logging.getLogger("TCPClient")
        self.host = host or CONFIG.NETWORK.HOST
        self.port_data = port_data or CONFIG.NETWORK.PORT_DATA
        self.port_cmd = port_cmd or CONFIG.NETWORK.PORT_CMD
        
        # Sockets
        self.data_socket: Optional[socket.socket] = None
        self.cmd_socket: Optional[socket.socket] = None
        
        # Threads
        self._running = False
        self._recv_thread: Optional[threading.Thread] = None
        self._cmd_recv_thread: Optional[threading.Thread] = None
        
        # Cola de comandos (como en tu código)
        self._cmd_queue: queue.Queue[dict] = queue.Queue()
        
        # Callbacks
        self.on_image_received: Optional[Callable[[Image.Image], None]] = None
        self.on_response_received: Optional[Callable[[ServerResponse], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        
        # Stats
        self._frame_count = 0
        self._last_stats_time = 0
    
    # =================================================================
    # CONEXIÓN
    # =================================================================
    
    def connect(self) -> bool:
        """Conecta ambos sockets."""
        try:
            logger.info(f"Conectando a {self.host}:{self.port_data} (datos) y {self.port_cmd} (cmd)...")
            
            # Socket de datos (8000)
            self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.data_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.data_socket.setsockopt(
                socket.SOL_SOCKET, 
                socket.SO_RCVBUF, 
                CONFIG.NETWORK.RCV_BUFFER_SIZE
            )
            self.data_socket.settimeout(CONFIG.NETWORK.TIMEOUT_SECONDS)
            self.data_socket.connect((self.host, self.port_data))
            self.data_socket.settimeout(None)
            
            # Socket de comandos (8001)
            self.cmd_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.cmd_socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_SNDBUF,
                CONFIG.NETWORK.SND_BUFFER_SIZE
            )
            self.cmd_socket.settimeout(CONFIG.NETWORK.TIMEOUT_SECONDS)
            self.cmd_socket.connect((self.host, self.port_cmd))
            self.cmd_socket.settimeout(None)
            
            self._running = True
            
            # Iniciar threads
            self._recv_thread = threading.Thread(target=self._receive_images, daemon=True)
            self._recv_thread.start()
            
            self._cmd_recv_thread = threading.Thread(target=self._receive_responses, daemon=True)
            self._cmd_recv_thread.start()
            
            # Thread para enviar comandos
            self._send_thread = threading.Thread(target=self._send_commands_loop, daemon=True)
            self._send_thread.start()
            
            logger.info("Conexión establecida (2 puertos)")
            return True
            
        except Exception as e:
            logger.error(f"Error conexión: {e}")
            self._notify_error(f"Connection failed: {e}")
            return False
    
    def disconnect(self):
        """Desconecta ambos sockets."""
        self._running = False
        
        for sock in [self.data_socket, self.cmd_socket]:
            if sock:
                try:
                    sock.close()
                except:
                    pass
        
        self.data_socket = None
        self.cmd_socket = None
        logger.info("Desconectado")
    
    def is_connected(self) -> bool:
        return self._running and self.data_socket is not None
    
    # =================================================================
    # RECEPCIÓN DE IMÁGENES (Puerto 8000)
    # =================================================================

    def start_image_stream(self):
        """
        Inicia recepción de imágenes.
        En realidad el thread ya está corriendo desde connect(),
        pero este método es para compatibilidad con la API esperada.
        """
        # El thread de recepción ya se inició en connect() vía _receive_images
        # Este método es un no-op explícito para claridad
        self.logger.debug("Image stream ya está activo (iniciado en connect)")
        pass

    def stop_image_stream(self):
        """Detiene recepción de imágenes."""
        self._streaming = False
        self._running = False
    
    def _receive_images(self):
        """Loop de recepción de imágenes con header de 12 bytes."""
        while self._running:
            try:
                # 1. Recibir header de 12 bytes
                header = self._recv_exactly(self.data_socket, self.HEADER_SIZE)
                if not header:
                    raise ConnectionError("Servidor cerró conexión de datos")
                
                # 2. Parsear header
                timestamp, size = struct.unpack('!dI', header)
                
                if size > 2_000_000 or size < 100:
                    raise ValueError(f"Tamaño de imagen inválido: {size}")
                
                # 3. Recibir datos JPEG
                jpeg_data = self._recv_exactly(self.data_socket, size)
                if not jpeg_data:
                    raise ConnectionError("Conexión perdida durante recepción de imagen")
                
                # 4. Procesar imagen
                self._process_image(jpeg_data)
                
                # 5. Stats
                self._frame_count += 1
                
            except Exception as e:
                if self._running:
                    logger.error(f"Error recepción imagen: {e}")
                    self._notify_error(f"Image receive error: {e}")
                break
        
        self._running = False
    
    def _recv_exactly(self, sock: socket.socket, n_bytes: int) -> Optional[bytes]:
        """Recibe exactamente n_bytes del socket."""
        data = bytearray()
        while len(data) < n_bytes and self._running:
            try:
                chunk = sock.recv(n_bytes - len(data))
                if not chunk:
                    return None
                data.extend(chunk)
            except socket.timeout:
                continue
            except Exception:
                return None
        return bytes(data)
    
    def _process_image(self, jpeg_data: bytes):
        try:
            image = Image.open(BytesIO(jpeg_data))
            image.load()  # ← revienta aquí si está truncado, no en los suscriptores
            if self.on_image_received:
                self.on_image_received(image)
        except Exception as e:
            logger.warning(f"Frame descartado (JPEG inválido): {e}")
            # No se llama on_image_received → ningún suscriptor recibe el frame corrupto
        
    # =================================================================
    # COMANDOS (Puerto 8001)
    # =================================================================
    
    def send_command(self, cmd_dict: dict):
        """
        Encola comando para envío.
        El comando se enviará en el thread de envío.
        """
        self._cmd_queue.put(cmd_dict)
    
    def _send_commands_loop(self):
        """Thread que envía comandos de la cola."""
        while self._running:
            try:
                cmd = self._cmd_queue.get(timeout=0.1)
                if self.cmd_socket:
                    data = ProtocolEncoder.encode_command(cmd)
                    self.cmd_socket.sendall(data)
                    logger.debug(f"Comando enviado: {cmd}")
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error enviando comando: {e}")
    
    def _receive_responses(self):
        """Thread que recibe respuestas JSON línea por línea."""
        buffer = ""
        
        while self._running and self.cmd_socket:
            try:
                data = self.cmd_socket.recv(1024).decode('utf-8')
                if not data:
                    break
                
                buffer += data
                
                # Procesar líneas completas
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        response = ServerResponse.from_json_line(line)
                        self._process_response(response)
                        
            except Exception as e:
                if self._running:
                    logger.error(f"Error recibiendo respuesta: {e}")
                break
    
    def _process_response(self, response: ServerResponse):
        """Procesa respuesta del servidor."""
        logger.debug(f"Respuesta servidor: {response.status} - {response.message}")
        
        if self.on_response_received:
            self.on_response_received(response)
    
    # =================================================================
    # API DE ALTO NIVEL
    # =================================================================
    
    def send_exposure(self, exposure_us: int):
        """Envía exposición en microsegundos."""
        self.send_command({
            "action": "set_exposure",
            "value": int(exposure_us)
        })
    
    def send_gain(self, gain_raw: float):
        """
        Envía ganancia.
        gain_raw: valor real (1.0 - 16.0), se multiplica ×10 para protocolo
        """
        self.send_command({
            "action": "set_gain",
            "value": round(gain_raw * CONFIG.CAMERA.GAIN_MULTIPLIER)
        })
    
    def send_st4_pulse(self, direction: str, duration_ms: int):
        server_direction = CONFIG.ST4.DIRECTION_MAP.get(direction, direction)
        self.send_command({
            "action": "st4_pulse",
            "direction": server_direction,
            "duration_ms": int(duration_ms)
        })
        
    def _notify_error(self, message: str):
        if self.on_error:
            self.on_error(message)
    
    def get_stats(self) -> dict:
        return {
            'connected': self.is_connected(),
            'frames_received': self._frame_count
        }