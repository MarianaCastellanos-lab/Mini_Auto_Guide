# interface/state_renderer.py
"""
StateRenderer - Decide qué mostrar en la UI según el estado del sistema.
Separa completamente la lógica de presentación del renderizado.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, Set, Callable
from application.state_machine import SystemState
from enum import Enum, auto

logger = logging.getLogger("StateRender")

@dataclass(frozen=True)
class UIConfiguration:
    """
    Configuración inmutable de la UI para un estado específico.
    frozen=True hace que sea inmutable y hashable (seguro para caching).
    """
    status_message: str
    enabled_buttons: Set[str] = field(default_factory=set)
    panel_message: Optional[str] = None  # Si no None, muestra texto en panel
    warning_message: Optional[str] = None  # Mensaje si usuario clickea botón inválido
    show_star_overlay: bool = False
    graph_active: bool = False
    status_color: Optional[str] = None


class StateRenderer:
    """
    Mapeo único de estados a configuraciones UI.
    Esta es la única clase que sabe qué botones deben estar habilitados en cada estado.
    """
    
    # Configuración estática centralizada
    CONFIGS = {
        SystemState.VERIFICATION: UIConfiguration(
            status_message="Conectando a la cámara Raspberry...",
            enabled_buttons=set(),  # Ningún botón
            panel_message="Esperando conexión...",
            status_color="#444444"  # Gris
        ),
        
        SystemState.INITIAL_PARAMS: UIConfiguration(
            status_message="Ingrese escala de píxeles y distancia focal",
            enabled_buttons={'px_scale', 'dist_focal', 'bucle'},
            warning_message="Por favor ingrese escala de píxeles y distancia focal",
            panel_message="Configure los parámetros iniciales",
            status_color="#664411"  # Naranja/Amarillo
        ),
        
        SystemState.STAR_SELECTION: UIConfiguration(
            status_message="Seleccione una estrella y ajuste exposición/ganancia",
            enabled_buttons={
                'bucle', 'exposicion', 'ganancia', 'num_estrella',
                'calibrar'  # Escalas de gráfico siempre disponibles
            },
            panel_message="Ajuste exposición y seleccione estrella",
            graph_active=True,
            status_color="#116611"  # Verde oscuro
        ),
        
        SystemState.WAITING_USER: UIConfiguration(
            status_message="Sistema listo. Presione Start para guiar",
            enabled_buttons={
                'comenzar', 'detener',  # Detener por si acaso
                'agresividad', 'min_mo',
                'x_scale', 'y_scale'
            },
            warning_message="El sistema está listo para guiar. Si desea cambiar algo, reinicie.",
            panel_message="Ajuste agresividad y MinMo antes de iniciar",
            status_color="#116611"
        ),
        
        SystemState.CALIBRATION: UIConfiguration(
            status_message="Calibrando... Por favor espere",
            enabled_buttons=set(),  # Ninguno durante calibración
            warning_message="El sistema está calibrando, por favor espere",
            panel_message="Calibración en progreso...",
            graph_active=True,
            status_color="#661111"  # Rojo oscuro
        ),
        
        SystemState.GUIDING: UIConfiguration(
            status_message="Guiado activo - Monitoreando",
            enabled_buttons={
                'detener', 'exposicion', 'ganancia',
                'agresividad', 'min_mo',
                'x_scale', 'y_scale'
            },
            warning_message="Cambiar exposición/ganancia influye significativamente en el guiado. Cuidado.",
            panel_message="Guiado activo - Monitoreando errores",
            show_star_overlay=True,
            graph_active=True,
            status_color="#008800"  # Verde brillante
        ),
    }
    
    def __init__(self, main_window):
        """
        Inicializa el renderer con la ventana principal.
        
        Args:
            main_window: Instancia de MainWindow (la vista)
        """
        self.logger = logging.getLogger("StateRender")
        self.view = main_window
        self._current_state: Optional[SystemState] = None
        self._current_config: Optional[UIConfiguration] = None
        self._on_invalid_click: Optional[Callable[[str], None]] = None
    
    def render(self, state: SystemState):
        """
        Aplica la configuración correspondiente al estado.
        Es idempotente: si el estado no cambió, no re-renderiza innecesariamente.
        
        Args:
            state: El nuevo estado del sistema
        """
        if state == self._current_state:
            return  # Optimización: evita re-renders innecesarios
        
        config = self.CONFIGS.get(state)
        if not config:
            raise ValueError(f"Estado desconocido: {state}")
        
        self._current_state = state
        self._current_config = config
        
        self._apply_config(config)
    
    def _apply_config(self, config: UIConfiguration):
        """Aplica una configuración a la vista."""
        # 1. Actualizar barra de estado
        self.view.set_status_message(
            config.status_message, 
            color=config.status_color
        )
        
        # 2. Habilitar/deshabilitar botones
        # Primero deshabilitar todos, luego habilitar los permitidos
        all_buttons = {
            'calibrar', 'detener', 'comenzar',
            'px_scale', 'dist_focal', 'bucle',
            'exposicion', 'num_estrella', 'ganancia',
            'x_scale', 'y_scale', 'agresividad', 'min_mo'
        }
        
        # Deshabilitar todos primero
        for btn in all_buttons:
            self.view.set_button_enabled(btn, False)
        
        # Habilitar los permitidos
        for btn in config.enabled_buttons:
            self.view.set_button_enabled(btn, True)
        
        # 3. Panel de configuración
        if config.panel_message:
            self.view.show_text_in_panel(config.panel_message)
        
        # 4. Configurar manejador de clicks inválidos
        if config.warning_message:
            self._setup_invalid_click_handler(config.warning_message)
    
    def _setup_invalid_click_handler(self, warning: str):
        """Configura el manejador para clicks en botones deshabilitados."""
        # Nota: Esto es complejo en Tkinter porque los botones disabled no generan events
        # Alternativa: mantenerlos enabled visualmente pero bloquear la acción
        # Por ahora, solo mostramos el warning si el usuario intenta interactuar
        # con algo que no está en enabled_buttons (esto se maneja en el adaptador)
        pass
    
    def handle_button_click(self, button_name: str) -> bool:
        """Verifica si un click de botón es válido en el estado actual."""
        self.logger = logging.getLogger("StateRenderer")  # Agrega al inicio del método
        self.logger.debug(f"Validando click: '{button_name}' contra habilitados: {self._current_config.enabled_buttons if self._current_config else 'None'}")
        
        if not self._current_config:
            return False
        
        is_valid = button_name in self._current_config.enabled_buttons
        
        if not is_valid and self._current_config.warning_message:
            self.view.show_warning(self._current_config.warning_message)
        
        return is_valid
    
    def get_config(self, state: SystemState) -> Optional[UIConfiguration]:
        """Obtiene la configuración para un estado específico."""
        return self.CONFIGS.get(state)
    
    def get_current_state(self) -> Optional[SystemState]:
        """Retorna el estado actualmente renderizado."""
        return self._current_state