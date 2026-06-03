#!/usr/bin/env python3
"""
Servidor IMX477 | Zero 2W Optimizado + Control ST4 CONCURRENTE
Autoguiado - Fase ST4 Concurrente - GPIO Funcionales: 23, 24, 25, 8
Pulsos concurrentes permitidos (N+E, N+W, S+E, S+W), opuestos bloqueados
"""

import socket
import struct
import time
import io
import threading
import queue
import json
import numpy as np
from PIL import Image
import cv2
from picamera2 import Picamera2

# ST4 Imports - Usar gpiod (más confiable en Pi OS Bookworm que gpiozero)
GPIO_AVAILABLE = False

try:
    import gpiod
    from gpiod.line import Direction, Value
    GPIO_AVAILABLE = True
    print("✓ Usando gpiod para GPIO")
except ImportError:
    print("⚠ Advertencia: gpiod no disponible, ST4 simulado")
    print("  Instala con: sudo apt install python3-libgpiod")

HOST = '0.0.0.0'
PORT_DATA = 8000
PORT_CMD = 8001
JPEG_QUALITY = 92

# GPIO ST4 Configuration - Pines verificados funcionales
ST4_PINS = {
    'norte': 23,   # GPIO 23 - Pin físico 16 (DEC+)
    'sur': 24,     # GPIO 24 - Pin físico 18 (DEC-)
    'este': 25,    # GPIO 25 - Pin físico 22 (RA+)
    'oeste': 8     # GPIO 8  - Pin físico 24 (RA-) (REQUIERE SPI DESHABILITADO)
}

# Grupos de ejes opuestos
OPPOSITE_PAIRS = {
    'norte': 'sur',
    'sur': 'norte',
    'este': 'oeste',
    'oeste': 'este'
}

# Ejes por grupo (RA vs DEC)
AXIS_GROUPS = {
    'norte': 'dec',
    'sur': 'dec',
    'este': 'ra',
    'oeste': 'ra'
}


class ST4ControllerConcurrent:
    """Controlador ST4 concurrente: permite N+E, bloquea N+S"""
    
    def __init__(self):
        self.lines = None
        self.gpio_available = False
        self.chip = None
        
        # Locks por eje (RA y DEC son independientes)
        self.axis_locks = {
            'ra': threading.Lock(),
            'dec': threading.Lock()
        }
        
        # Tracking de direcciones activas por eje
        self.active_directions = {
            'ra': None,   # 'este' o 'oeste' o None
            'dec': None   # 'norte' o 'sur' o None
        }
        self.active_lock = threading.Lock()
        
        # Cola de pulsos concurrente (por eje)
        self.pulse_queues = {
            'ra': queue.Queue(),
            'dec': queue.Queue()
        }
        
        self.running = True
        
        if GPIO_AVAILABLE:
            try:
                self._setup_gpio()
            except Exception as e:
                print(f"✗ Error setup GPIO ST4: {e}")
                print(f"  Asegúrate de que SPI está deshabilitado: sudo raspi-config nonint do_spi 1")
                self.gpio_available = False
        
        # Workers por eje (2 hilos: uno para RA, uno para DEC)
        self.workers = {}
        for axis in ['ra', 'dec']:
            t = threading.Thread(target=self._pulse_worker, args=(axis,), daemon=True, name=f"ST4-{axis.upper()}")
            t.start()
            self.workers[axis] = t
    
    def _setup_gpio(self):
        """Configura GPIO usando gpiod"""
        self.chip = gpiod.Chip("/dev/gpiochip0")
        gpio_nums = list(ST4_PINS.values())
        
        # Request líneas como salida, inicialmente apagadas
        self.lines = self.chip.request_lines(
            consumer="st4-server-concurrent",
            config={tuple(gpio_nums): gpiod.LineSettings(
                direction=Direction.OUTPUT, 
                output_value=Value.INACTIVE
            )}
        )
        
        self.gpio_available = True
        print(f"✓ ST4 GPIO inicializados (MODO CONCURRENTE): {ST4_PINS}")
        print(f"  Pines: NORTE=23(16), SUR=24(18), ESTE=25(22), OESTE=8(24)")
        print(f"  Ejes independientes: RA(ESTE/OESTE) | DEC(NORTE/SUR)")
    
    def _pulse_worker(self, axis):
        """Worker thread dedicado por eje (RA o DEC)"""
        print(f"  Worker {axis.upper()} iniciado")
        while self.running:
            try:
                direction, duration_ms = self.pulse_queues[axis].get(timeout=0.1)
                result = self._execute_pulse(direction, duration_ms)
                # El resultado se maneja vía el sistema de respuestas asíncronas
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error en worker {axis}: {e}")
    
    def _execute_pulse(self, direction, duration_ms):
        """Ejecuta pulso ST4 con precisión (llamado desde worker de eje)"""
        duration_ms = max(50, min(5000, duration_ms))
        duration_s = duration_ms / 1000.0
        
        gpio_num = ST4_PINS[direction]
        axis = AXIS_GROUPS[direction]
        
        print(f"ST4 [{axis.upper()}]: {direction.upper()} {duration_ms}ms")
        
        if self.gpio_available and self.lines:
            try:
                # Encender (HIGH)
                self.lines.set_value(gpio_num, Value.ACTIVE)
                
                # Timing preciso (busy-wait para precisión de milisegundos)
                start = time.perf_counter()
                while (time.perf_counter() - start) < duration_s:
                    pass
                
                # Apagar (LOW)
                self.lines.set_value(gpio_num, Value.INACTIVE)
                
                actual_ms = int((time.perf_counter() - start) * 1000)
                
                # Liberar el eje
                with self.active_lock:
                    if self.active_directions[axis] == direction:
                        self.active_directions[axis] = None
                
                return {
                    "status": "ok", 
                    "direction": direction, 
                    "duration_ms": duration_ms,
                    "actual_ms": actual_ms,
                    "axis": axis
                }
                
            except Exception as e:
                with self.active_lock:
                    if self.active_directions[axis] == direction:
                        self.active_directions[axis] = None
                return {"status": "error", "message": str(e), "direction": direction}
        else:
            # Modo simulado
            time.sleep(duration_s)
            with self.active_lock:
                if self.active_directions[axis] == direction:
                    self.active_directions[axis] = None
            return {
                "status": "simulated", 
                "direction": direction, 
                "duration_ms": duration_ms,
                "axis": axis
            }
    
    def can_execute(self, direction):
        """Verifica si la dirección puede ejecutarse (no hay opuesto activo)"""
        axis = AXIS_GROUPS[direction]
        opposite = OPPOSITE_PAIRS[direction]
        opposite_axis = AXIS_GROUPS[opposite]
        
        with self.active_lock:
            # Verificar si hay algo activo en el mismo eje
            current = self.active_directions[axis]
            if current is not None:
                return False, f"Eje {axis} ocupado por {current}"
            
            # Verificar si el opuesto está activo (no debería pasar por diseño, pero por seguridad)
            if self.active_directions[opposite_axis] == opposite:
                return False, f"Dirección opuesta {opposite} activa"
            
            # Marcar como activo
            self.active_directions[axis] = direction
            return True, "ok"
    
    def pulse(self, direction, duration_ms):
        """
        Encola un pulso ST4 (no bloqueante, concurrente entre ejes)
        Retorna inmediatamente con estado queued o error
        """
        direction = direction.lower()
        if direction not in ST4_PINS:
            return {
                "status": "error", 
                "message": f"Dirección inválida: {direction}. Use: {list(ST4_PINS.keys())}"
            }
        
        axis = AXIS_GROUPS[direction]
        
        # Verificar si se puede ejecutar
        can_exec, msg = self.can_execute(direction)
        if not can_exec:
            return {
                "status": "rejected",
                "direction": direction,
                "message": msg
            }
        
        # Encolar en la cola del eje correspondiente
        self.pulse_queues[axis].put((direction, duration_ms))
        
        return {
            "status": "queued", 
            "direction": direction, 
            "duration_ms": duration_ms,
            "axis": axis,
            "concurrent": True
        }
    
    def get_status(self):
        """Retorna estado actual de los ejes"""
        with self.active_lock:
            return {
                'ra': self.active_directions['ra'],
                'dec': self.active_directions['dec'],
                'gpio_available': self.gpio_available
            }
    
    def stop(self):
        self.running = False
        
        # Esperar workers
        for axis, worker in self.workers.items():
            worker.join(timeout=2.0)
        
        # Apagar todos los pines y liberar
        if self.gpio_available and self.lines:
            try:
                for gpio in ST4_PINS.values():
                    self.lines.set_value(gpio, Value.INACTIVE)
                self.lines.release()
                print("✓ GPIO ST4 liberados")
            except Exception as e:
                print(f"Error liberando GPIO: {e}")


class CameraServer:
    def __init__(self):
        self.picam2 = None
        self.running = False
        self.current_exposure = 10000
        self.current_gain = 1.0
        self.lock = threading.Lock()
        self.st4 = ST4ControllerConcurrent()
        
        # Sistema de respuestas asíncronas para ST4
        self.st4_responses = queue.Queue()
        self.response_thread = threading.Thread(target=self._response_sender, daemon=True)
        self.response_thread.start()
        self.current_cmd_conn = None
        self.cmd_lock = threading.Lock()
        
    def start_camera(self):
        """Configuración optimizada Zero 2W - SIN CAMBIOS"""
        self.picam2 = Picamera2()
        
        config = self.picam2.create_video_configuration(
            main={"size": (1014, 760), "format": "RGB888"},
            controls={
                "AwbEnable": True,
                "AeEnable": False,
                "ExposureTime": self.current_exposure,
                "AnalogueGain": self.current_gain,
                "FrameDurationLimits": (1000, 2500000),
            }
        )
        self.picam2.configure(config)
        self.picam2.start()

        time.sleep(0.5)
        
        self.picam2.set_controls({
            "ExposureTime": self.current_exposure,
            "AnalogueGain": self.current_gain,
        })
        
        time.sleep(0.2)
        
        metadata = self.picam2.capture_metadata()
        actual = metadata.get('ExposureTime', 0)
        print(f"✓ Cámara lista (Zero 2W Optimizado)")
        print(f"  Resolución: 1014×760 (binning 2×2)")
        print(f"  Exposición: {actual}µs")
        print(f"  JPEG quality: {JPEG_QUALITY}%")
        print(f"  ST4: {'Hardware Concurrente' if self.st4.gpio_available else 'Simulado'}")

        return True
    
    def get_system_stats(self):
        """Lectura ligera de /proc sin dependencias externas"""
        # CPU: leer /proc/stat
        try:
            with open('/proc/stat', 'r') as f:
                line = f.readline()
                fields = list(map(int, line.split()[1:]))
                idle = fields[3]
                total = sum(fields)
                cpu_percent = ((total - idle) / total) * 100 if total else 0
        except:
            cpu_percent = 0.0
        
        # RAM: /proc/meminfo
        try:
            with open('/proc/meminfo', 'r') as f:
                mem_total = int(f.readline().split()[1])  # kB
                mem_available = int(f.readline().split()[1])  # kB (aprox)
                mem_sys_percent = ((mem_total - mem_available) / mem_total) * 100
        except:
            mem_sys_percent = 0.0
        
        # RAM del proceso: /proc/self/status
        proc_mem_mb = 0.0
        try:
            with open('/proc/self/status', 'r') as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        proc_mem_mb = int(line.split()[1]) / 1024  # kB → MB
                        break
        except:
            pass
        
        # Temperatura
        temp = None
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = round(int(f.read().strip()) / 1000.0, 1)
        except:
            pass
        
        return {
            'cpu': round(cpu_percent, 1),
            'ram_sys': round(mem_sys_percent, 1),
            'ram_proc': round(proc_mem_mb, 1),
            'temp': temp
        }


    def capture_and_compress(self):
        """Captura optimizada: OpenCV NEON + JPEG turbo - SIN CAMBIOS"""
        with self.lock:
            request = self.picam2.capture_request()
            try:
                rgb = request.make_array('main')
                gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
                img = Image.fromarray(gray, mode='L')
                
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=JPEG_QUALITY, optimize=True)
                return buffer.getvalue()
            finally:
                request.release()
    
    def _response_sender(self):
        """Hilo dedicado para enviar respuestas ST4 asíncronas al cliente"""
        while self.running:
            try:
                response = self.st4_responses.get(timeout=0.1)
                with self.cmd_lock:
                    if self.current_cmd_conn:
                        try:
                            msg = json.dumps(response) + '\n'
                            self.current_cmd_conn.send(msg.encode())
                        except:
                            pass
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error en response sender: {e}")
    
    def _handle_st4_completion(self, result):
        """Callback para cuando termina un pulso (llamado desde workers)"""
        self.st4_responses.put(result)
    
    def handle_commands(self):
        """Socket de comandos - thread separado con soporte ST4 concurrente"""
        cmd_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cmd_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        cmd_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
        cmd_socket.bind((HOST, PORT_CMD))
        cmd_socket.listen(1)
        
        print(f"✓ Comandos en puerto {PORT_CMD}")
        
        while self.running:
            try:
                conn, addr = cmd_socket.accept()
                print(f"Cliente comandos: {addr}")
                
                with self.cmd_lock:
                    self.current_cmd_conn = conn
                
                with conn:
                    conn.settimeout(0.5)
                    buffer = ""
                    while self.running:
                        try:
                            data = conn.recv(1024).decode('utf-8')
                            if not data:
                                break
                            
                            buffer += data
                            
                            # Procesar líneas completas
                            while '\n' in buffer:
                                line, buffer = buffer.split('\n', 1)
                                if not line.strip():
                                    continue
                                try:
                                    cmd = json.loads(line)
                                    response = self.process_command(cmd)
                                    response_str = json.dumps(response) + '\n'
                                    conn.send(response_str.encode())
                                except json.JSONDecodeError:
                                    continue
                                    
                        except socket.timeout:
                            continue
                        except Exception as e:
                            try:
                                conn.send(json.dumps({"error": str(e)}).encode() + b'\n')
                            except:
                                break
                
                with self.cmd_lock:
                    self.current_cmd_conn = None
                            
            except Exception as e:
                if self.running:
                    print(f"Error cmd: {e}")
                    
    def process_command(self, cmd):
        """Procesamiento de comandos - ST4 concurrente añadido"""
        action = cmd.get('action')
        
        if action == 'set_exposure':
            value = int(cmd.get('value', 10000))
            value = max(100, min(2000000, value))
            try:
                with self.lock:
                    self.picam2.set_controls({"ExposureTime": value})
                    self.current_exposure = value
                return {"status": "ok", "exposure": value}
            except Exception as e:
                return {"status": "error", "message": str(e)}
                
        elif action == 'set_gain':
            value = float(cmd.get('value', 1.0))
            value = max(1.0, min(16.0, value))
            try:
                with self.lock:
                    self.picam2.set_controls({"AnalogueGain": value})
                    self.current_gain = value
                return {"status": "ok", "gain": value}
            except Exception as e:
                return {"status": "error", "message": str(e)}
                
        elif action == 'get_status':
            st4_status = self.st4.get_status()
            return {
                "status": "ok",
                "exposure": self.current_exposure,
                "gain": self.current_gain,
                "st4_available": self.st4.gpio_available,
                "st4_active_ra": st4_status['ra'],
                "st4_active_dec": st4_status['dec'],
                "resolution": [1014, 760]
            }
        
        elif action == 'st4_pulse':
            direction = cmd.get('direction', '').lower()
            duration = int(cmd.get('duration_ms', 100))
            return self.st4.pulse(direction, duration)
        
        elif action == 'st4_stop':
            # Detener todos los pulsos activos
            return {"status": "ok", "message": "ST4 detenido (no implementado en modo concurrente)"}
        
        else:
            return {"status": "error", "message": "Comando desconocido"}
            
    def stream_images(self):
        """Streaming con buffers TCP optimizados - SIN CAMBIOS"""
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
        server_socket.bind((HOST, PORT_DATA))
        server_socket.listen(1)
        
        print(f"✓ Imágenes en puerto {PORT_DATA}")
        print("Esperando cliente...")
        
        conn, addr = server_socket.accept()
        print(f"Cliente imágenes: {addr}")
        
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        
        frame_count = 0
        start_time = time.time()
        
        try:
            while self.running:
                t0 = time.time()
                jpeg = self.capture_and_compress()
                capture_time = (time.time() - t0) * 1000
                
                timestamp = time.time()
                header = struct.pack('!dI', timestamp, len(jpeg))
                
                try:
                    conn.sendall(header)
                    conn.sendall(jpeg)
                except:
                    print("Cliente desconectado")
                    break
                
                frame_count += 1
                if frame_count % 30 == 0:
                    fps = frame_count / (time.time() - start_time)
                    stats = self.get_system_stats()
                    temp_str = f" | Temp: {stats['temp']}°C" if stats['temp'] else ""
                    
                    print(f"[{frame_count}] FPS: {fps:.1f} | "
                          f"Size: {len(jpeg)/1024:.1f} KB | "
                          f"Capture: {capture_time:.1f} ms | "
                          f"CPU: {stats['cpu']:.0f}% | "
                          f"RAM: {stats['ram_proc']}MB ({stats['ram_sys']:.0f}%){temp_str}")
                
                sleep_time = max(0.02, min(0.2, self.current_exposure / 1000000))
                time.sleep(sleep_time)
                
        finally:
            conn.close()
            server_socket.close()
            
    def run(self):
        print("=== Servidor IMX477 | Zero 2W + ST4 CONCURRENTE ===")
        print("GPIO ST4: 23(N) 24(S) 25(E) 8(W) - SPI debe estar deshabilitado")
        print("Modo: Ejes RA y DEC independientes (pulsos concurrentes permitidos)")
        
        if not self.start_camera():
            return
            
        self.running = True
        
        cmd_thread = threading.Thread(target=self.handle_commands, daemon=True)
        cmd_thread.start()
        
        try:
            self.stream_images()
        except KeyboardInterrupt:
            print("\nDeteniendo...")
        finally:
            self.running = False
            self.st4.stop()
            self.picam2.stop()
            self.picam2.close()
            print("✓ Recursos liberados")

if __name__ == "__main__":
    server = CameraServer()
    server.run()