# interface/adapters.py
"""
UIEventAdapter - Puente entre eventos de la UI y la lógica de aplicación.
COMPATIBLE con maqueta_0.py y panel_manager_0.py
"""

import logging
from typing import Callable, Optional, Dict, TYPE_CHECKING
from dataclasses import dataclass
from enum import Enum, auto

from config import CONFIG

# Evitar import circular
if TYPE_CHECKING:
    from application.star_selection_service import StarSelectionService

logger = logging.getLogger("UIEventAdapter")


class UICommand(Enum):
    """Comandos que la UI puede generar."""
    LOOP_BUTTON = auto()
    CALIBRATE_BUTTON = auto()
    START_BUTTON = auto()
    STOP_BUTTON = auto()
    SET_PX_SCALE = auto()
    SET_FOCAL_DIST = auto()
    SET_EXPOSURE = auto()
    SET_GAIN = auto()
    SET_STAR_NUMBER = auto()
    SET_AGGRESSIVITY = auto()
    SET_MIN_MO = auto()
    SET_X_SCALE = auto()
    SET_Y_SCALE = auto()


@dataclass
class CommandEvent:
    """Evento de comando con datos asociados."""
    command: UICommand
    data: Optional[dict] = None


class UIEventAdapter:
    """
    Adaptador que convierte clicks de botones en comandos de aplicación.
    Basado en panel_manager_0.py funcional.
    """
    
    BUTTON_COMMAND_MAP = {
        "Automatic Calibration": UICommand.CALIBRATE_BUTTON,
        "Stop": UICommand.STOP_BUTTON,
        "Start": UICommand.START_BUTTON,
        "Cam Gde Px Scale": UICommand.SET_PX_SCALE,
        "Dist Focal": UICommand.SET_FOCAL_DIST,
        "Bucle": UICommand.LOOP_BUTTON,
        "Expo Time": UICommand.SET_EXPOSURE,
        "Num Star": UICommand.SET_STAR_NUMBER,
        "Gain": UICommand.SET_GAIN,
        "X scale": UICommand.SET_X_SCALE,
        "Y scale": UICommand.SET_Y_SCALE,
        "Agresividad": UICommand.SET_AGGRESSIVITY,
        "MinMo": UICommand.SET_MIN_MO,
    }

    BUTTON_NAME_TO_INTERNAL = {
        "Automatic Calibration": "calibrar",
        "Stop": "detener", 
        "Start": "comenzar",
        "Cam Gde Px Scale": "px_scale",
        "Dist Focal": "dist_focal",
        "Bucle": "bucle",
        "Expo Time": "exposicion",
        "Num Star": "num_estrella",
        "Gain": "ganancia",
        "X scale": "x_scale",
        "Y scale": "y_scale",
        "Agresividad": "agresividad",
        "MinMo": "min_mo",
    }
    
    def __init__(self, state_renderer, command_handler: Callable[[CommandEvent], None], star_selection_service=None):
        self.logger = logging.getLogger("UIEventAdapter")
        self.state_renderer = state_renderer
        self.command_handler = command_handler
        self._pending_value = None  # Para almacenar valor temporal
        self._star_selection_service = star_selection_service  # NUEVO: guardar referencia
        self._star_options_map = {}  # NUEVO: mapeo de opciones a IDs
    
    def on_button_click(self, button_name: str):
        """Maneja click de botón desde la UI."""
        self.logger.debug(f"Click detectado: {button_name}")
        
        # Convertir nombre del botón a nombre interno
        internal_name = self.BUTTON_NAME_TO_INTERNAL.get(button_name, button_name)
        self.logger.debug(f"Nombre interno: {internal_name}")
        
        # Validar si el click es permitido en el estado actual
        if not self.state_renderer.handle_button_click(internal_name):
            self.logger.debug(f"Click bloqueado para estado actual")
            return
        
        # Convertir a comando
        command = self.BUTTON_COMMAND_MAP.get(button_name)
        if not command:
            self.logger.warning(f"Botón '{button_name}' no mapeado")
            return
        
        # Crear evento base (sin datos aún)
        event = CommandEvent(command=command)
        
        # Para comandos que requieren input, configurar panel primero
        if command in [UICommand.SET_PX_SCALE, UICommand.SET_FOCAL_DIST, 
                      UICommand.SET_EXPOSURE, UICommand.SET_GAIN,
                      UICommand.SET_STAR_NUMBER, UICommand.SET_X_SCALE,
                      UICommand.SET_Y_SCALE, UICommand.SET_AGGRESSIVITY,
                      UICommand.SET_MIN_MO]:
            self._setup_input_panel(command, button_name, event)
        else:
            # Comandos simples (banderas de estado)
            self.command_handler(event)
    
    def _setup_input_panel(self, command: UICommand, button_name: str, base_event: CommandEvent):
        """Configura el panel de entrada según el comando - BASADO en panel_manager_0.py"""
        view = self.state_renderer.view
        
        if command == UICommand.SET_PX_SCALE:
            # Input de texto para tamaño del píxel en µm
            def on_confirm(value):
                try:
                    float_val = float(value)
                    # Validar rango razonable para tamaño de píxel (0.1µm a 20µm)
                    if float_val < 0.1 or float_val > 20.0:
                        raise ValueError("Tamaño de píxel debe estar entre 0.1 y 20 µm")
                    # Actualizar evento con datos y enviar
                    base_event.data = {'value': float_val}
                    self.command_handler(base_event)
                    view.set_status_message(f"Tamaño de píxel guardado: {float_val} µm")
                except ValueError as e:
                    view.show_warning(str(e))
            
            # Valor por defecto: IMX477 = 1.55 µm
            default = 1.55
            view.create_text_input("Tamaño de Píxel (µm)", default, on_confirm)
        
        elif command == UICommand.SET_FOCAL_DIST:
            def on_confirm(value):
                try:
                    float_val = float(value)
                    base_event.data = {'value': float_val}
                    self.command_handler(base_event)
                    view.set_status_message(f"Focal guardada: {float_val}mm")
                except ValueError:
                    view.show_warning("Valor numérico inválido")
            
            default = 500
            view.create_text_input("Distancia Focal (mm)", default, on_confirm)
        
        elif command == UICommand.SET_EXPOSURE:
            # EXPOSICIÓN: Segundos en UI, convertir a microsegundos para servidor
            def on_confirm(value):
                try:
                    seconds = float(value)
                    if seconds < 0.001 or seconds > 2.0:
                        raise ValueError("Rango: 0.001s - 2.0s")
                    
                    exposure_us = int(seconds * 1_000_000)
                    exposure_us = max(CONFIG.CAMERA.MIN_EXPOSURE_US,
                                     min(CONFIG.CAMERA.MAX_EXPOSURE_US, exposure_us))
                    
                    base_event.data = {
                        'value': seconds,
                        'exposure_us': exposure_us
                    }
                    self.command_handler(base_event)
                    view.set_status_message(f"Exposición: {seconds}s")
                    
                    # Actualizar texto del botón (como en panel_manager_0)
                    if hasattr(view, '_btn_exposicion'):
                        view._btn_exposicion.configure(text=f"{seconds}s")
                        
                except ValueError as e:
                    view.show_warning(str(e))
            
            # Slider de 0.001 a 2.0 segundos
            view.create_slider(
                "Tiempo de Exposición (s)", 
                min_val=0.001, 
                max_val=2.0, 
                step=0.001,
                initial=0.01,  # 10ms default
                on_confirm=on_confirm
            )
        
        elif command == UICommand.SET_GAIN:
            # GANANCIA: Valor ×10 en UI (10-160), valor real (1.0-16.0) para servidor
            def on_confirm(value):
                try:
                    gain_value = float(value)  # 10, 20, ..., 160
                    min_slider = CONFIG.CAMERA.MIN_GAIN_RAW * CONFIG.CAMERA.GAIN_MULTIPLIER
                    max_slider = CONFIG.CAMERA.MAX_GAIN_RAW * CONFIG.CAMERA.GAIN_MULTIPLIER
                    
                    if gain_value < min_slider or gain_value > max_slider:
                        raise ValueError(f"Rango: {min_slider} - {max_slider}")
                    
                    gain_raw = gain_value / CONFIG.CAMERA.GAIN_MULTIPLIER
                    
                    base_event.data = {
                        'value': gain_value,
                        'gain_raw': gain_raw
                    }
                    self.command_handler(base_event)
                    view.set_status_message(f"Ganancia: {gain_raw:.1f}x")
                    
                except ValueError as e:
                    view.show_warning(str(e))
            
            # Slider de 10 a 160 (representa 1.0x a 16.0x)
            view.create_slider(
                "Ganancia (×10 = valor real)",
                min_val=10,
                max_val=160,
                step=10,
                initial=10,  # 1.0x default
                on_confirm=on_confirm
            )
        
        elif command == UICommand.SET_STAR_NUMBER:
            # Obtener estrellas detectadas del servicio
            stars = []
            if self._star_selection_service:
                stars = self._star_selection_service.get_detected_stars()
            
            if not stars:
                view.show_warning("No hay estrellas detectadas. Asegúrese de que la cámara esté enfocada.")
                return
            
            # Formatear para display: "1 (SNR: 32.5)" - Top 5 por SNR
            options = [f"{s['id']} (SNR: {s['snr']:.1f})" for s in stars[:5]]
            
            # Guardar mapeo para extracción de ID
            self._star_options_map = {opt: s['id'] for opt, s in zip(options, stars[:5])}

            def on_confirm(selected_str):
                star_id = self._star_options_map.get(selected_str, 0)
                if star_id > 0:
                    # Seleccionar en el servicio (guarda en contexto y publica evento)
                    if self._star_selection_service:
                        success = self._star_selection_service.select_star(star_id)
                        if success:
                            base_event.data = {'value': star_id}
                            self.command_handler(base_event)
                            view.set_status_message(f"Estrella {star_id} seleccionada")
                        else:
                            view.show_warning("Error seleccionando estrella")
                else:
                    view.show_warning("Selección inválida")
            
            view.create_list_selector("Seleccionar Estrella (Top 5 por SNR)", options, on_confirm)
        
        elif command == UICommand.SET_X_SCALE:
            def on_confirm(value):
                base_event.data = {'value': float(value)}
                self.command_handler(base_event)
                view.set_status_message(f"Escala X: {int(value)} muestras")
            
            view.create_step_adjustment("Escala X (muestras)", 100, on_confirm)
        
        elif command == UICommand.SET_Y_SCALE:
            def on_confirm(value):
                base_event.data = {'value': float(value)}
                self.command_handler(base_event)
                view.set_status_message(f"Escala Y: ±{float(value)} arcsec")
            
            view.create_step_adjustment("Escala Y (arcsec)", 5.0, on_confirm)
        
        elif command == UICommand.SET_AGGRESSIVITY:
            def on_confirm(value):
                base_event.data = {'value': float(value)}
                self.command_handler(base_event)
                view.set_status_message(f"Agresividad: {int(value)}%")
            
            view.create_step_adjustment("Agresividad (%)", 70, on_confirm)
        
        elif command == UICommand.SET_MIN_MO:
            def on_confirm(value):
                base_event.data = {'value': float(value)}
                self.command_handler(base_event)
                view.set_status_message(f"MinMo: {float(value)} arcsec")
            
            view.create_step_adjustment("Minimum Movement (arcsec)", 0.2, on_confirm)
    
    def bind_to_window(self, window):
        """Conecta los callbacks de la ventana a este adaptador."""
        window.on_button_click = self.on_button_click