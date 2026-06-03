# application/state_machine.py
"""
Máquina de estados - Orquestador del sistema.
NO contiene lógica de negocio, solo coordina los servicios.
"""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Callable, Optional, Dict, Any
from shared.event_bus import EventType
import threading
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("StateMachine")


class SystemState(Enum):
    """Los 6 estados del sistema de autoguiado."""
    VERIFICATION = 1
    INITIAL_PARAMS = 2
    STAR_SELECTION = 3
    WAITING_USER = 4
    CALIBRATION = 5
    GUIDING = 6


@dataclass
class StateTransition:
    """Definición de transición entre estados."""
    target: SystemState
    condition: Callable[[], bool]
    action: Optional[Callable] = None


class StateMachine:
    """
    Máquina de estados declarativa.
    Solo orquesta, la lógica real está en los servicios.
    """
    
    def __init__(self, context_manager, services, event_bus):
        """
        Args:
            context_manager: ContextManager para acceso a datos globales
            services: Dict con 'star_selection', 'calibration', 'guiding'
            event_bus: EventBus para comunicación con UI
        """
        self.logger = logging.getLogger("StateMachine")
        self.ctx = context_manager
        self.services = services
        self.event_bus = event_bus
        
        self._state = SystemState.VERIFICATION
        self._previous_state: Optional[SystemState] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # Callback para notificar cambio de estado a la UI
        self.on_state_change: Optional[Callable[[SystemState, SystemState], None]] = None
        
        # Definir grafo de transiciones
        self._transitions = self._build_transitions()
        
        logger.info("StateMachine inicializada")
    
    def _build_transitions(self) -> Dict[SystemState, list]:
        """Define el grafo de estados válidos."""
        return {
            SystemState.VERIFICATION: [
                StateTransition(
                    target=SystemState.INITIAL_PARAMS,
                    condition=self._check_connection_ok,
                    action=self._on_enter_initial_params
                )
            ],
            
            SystemState.INITIAL_PARAMS: [
                StateTransition(
                    target=SystemState.STAR_SELECTION,
                    condition=self._check_initial_params_complete,
                    action=self._on_enter_star_selection
                )
            ],
            
            SystemState.STAR_SELECTION: [
                StateTransition(
                    target=SystemState.CALIBRATION,
                    condition=self._check_star_selected_and_calibrate_clicked,
                    action=self._on_enter_calibration
                )
            ],
            
            SystemState.CALIBRATION: [
                StateTransition(
                    target=SystemState.WAITING_USER,
                    condition=self._check_calibration_complete,
                    action=self._on_enter_waiting_user
                )
            ],
            
            SystemState.WAITING_USER: [
                StateTransition(
                    target=SystemState.GUIDING,
                    condition=self._check_start_clicked,
                    action=self._on_enter_guiding
                )
            ],
            
            SystemState.GUIDING: [
                StateTransition(
                    target=SystemState.WAITING_USER,
                    condition=self._check_stop_clicked,
                    action=self._on_exit_guiding
                )
            ]
        }
    
    # =================================================================
    # CONDICIONES DE TRANSICIÓN (usan el contexto)
    # =================================================================
    
    def _check_connection_ok(self) -> bool:
        """Estado 1→2: ¿Conexión establecida?"""
        with self.ctx.read() as ctx:
            is_connected = ctx.connection_status == "connected"
            self.logger.debug(f"Check connection: status='{ctx.connection_status}', result={is_connected}")
            return is_connected
    
    def _check_initial_params_complete(self) -> bool:
        """Estado 2→3: ¿Parámetros ingresados Y botón bucle clickeado?"""
        with self.ctx.read() as ctx:
            has_params = ctx.user.px_scale > 0 and ctx.user.focal_distance > 0
            clicked = ctx.flags.loop_button_clicked
            #self.logger.debug(f"=== CHECK 2→3 === px_scale={ctx.user.px_scale}, focal={ctx.user.focal_distance}, has_params={has_params}, loop_clicked={clicked}")
            return has_params and clicked
    
    def _check_star_selected_and_calibrate_clicked(self) -> bool:
        """Estado 3→5: ¿Estrella seleccionada Y botón calibrar clickeado?"""
        with self.ctx.read() as ctx:
            has_star = ctx.star.num_estrella > 0
            clicked = ctx.flags.calibrate_button_clicked
            #self.logger.debug(f"CHECK 3→5: has_star={has_star} (num_estrella={ctx.star.num_estrella}), clicked={clicked}")
            return has_star and clicked
    
    def _check_calibration_complete(self) -> bool:
        """Estado 5→4: ¿Calibración finalizó?"""
        return self.services['calibration'].is_complete()
    
    def _check_start_clicked(self) -> bool:
        """Estado 4→6: ¿Botón start clickeado?"""
        with self.ctx.read() as ctx:
            return ctx.flags.start_button_clicked
    
    def _check_stop_clicked(self) -> bool:
        """Estado 6→4: ¿Botón stop clickeado?"""
        with self.ctx.read() as ctx:
            return ctx.flags.stop_button_clicked
    
    # =================================================================
    # ACCIONES DE ENTRADA/SALIDA DE ESTADOS
    # =================================================================
    
    def _on_enter_initial_params(self):
        """Al entrar a estado 2: Limpiar flags."""
        with self.ctx.write() as ctx:
            ctx.flags.loop_button_clicked = False
            # Asegurar que la conexión sigue marcada como conectada
            # para que no vuelva a VERIFICATION
            if ctx.connection_status != "connected":
                ctx.connection_status = "connected"
        
        self.logger.info("Entrando a estado INITIAL_PARAMS")
    
    def _on_enter_star_selection(self):
        """Al entrar a estado 3: Iniciar detección de estrellas."""
        logger.info("Iniciando detección de estrellas (dummy)")
        self.services['star_selection'].start_detection()
    
    def _on_enter_calibration(self):
        """Al entrar a estado 5: Iniciar calibración."""
        logger.info("Iniciando calibración (dummy)")
        with self.ctx.write() as ctx:
            ctx.flags.calibrate_button_clicked = False
        
        # Iniciar servicio de calibración (async)
        self.services['calibration'].start_calibration()
    
    def _on_enter_waiting_user(self):
        """Al entrar a estado 4: Limpiar flag de start."""
        with self.ctx.write() as ctx:
            ctx.flags.start_button_clicked = False
    
    def _on_enter_guiding(self):
        """Al entrar a estado 6: Iniciar guiado."""
        logger.info("Iniciando guiado (dummy)")
        with self.ctx.write() as ctx:
            ctx.flags.start_button_clicked = False
        
        self.services['guiding'].start_guiding()
    
    def _on_exit_guiding(self):
        """Al salir de estado 6: Detener guiado."""
        logger.info("Deteniendo guiado")
        with self.ctx.write() as ctx:
            ctx.flags.stop_button_clicked = False
        
        self.services['guiding'].stop_guiding()
    
    # =================================================================
    # CONTROL PRINCIPAL
    # =================================================================
    
    def start(self):
        """Inicia la máquina de estados en thread separado."""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("StateMachine iniciada")
    
    def stop(self):
        """Detiene la máquina de estados."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
    
    def _run_loop(self):
        """Loop principal de la máquina de estados."""
        while self._running:
            self.tick()
            time.sleep(0.1)  # 10Hz es suficiente para estados
    
    def tick(self):
        """
        Ejecuta un ciclo: verifica transiciones y cambia estado si aplica.
        Solo UNA transición por tick, y solo si el estado actual tiene transiciones válidas.
        """
        # Si ya estamos en un cambio de estado, no hacer nada (evita reentrancia)
        if not hasattr(self, '_changing_state'):
            self._changing_state = False
        
        if self._changing_state:
            return
        
        transitions = self._transitions.get(self._state, [])
        
        for trans in transitions:
            if trans.condition():
                self._change_state(trans.target, trans.action)
                break  # Solo una transición por tick
    
    def _change_state(self, new_state: SystemState, action=None):
        """Cambia de estado de forma segura (no reentrante)."""
        # Marcar que estamos en cambio de estado
        self._changing_state = True
        
        try:
            old_state = self._state
            self._previous_state = old_state
            self._state = new_state
            
            self.logger.info(f"TRANSICIÓN: {old_state.name} → {new_state.name}")
            
            # Ejecutar acción asociada
            if action:
                try:
                    action()
                except Exception as e:
                    self.logger.error(f"Error en acción de estado: {e}")
            
            # Notificar a UI vía callback directo y event bus
            if self.on_state_change:
                try:
                    self.on_state_change(old_state, new_state)
                except Exception as e:
                    self.logger.error(f"Error en on_state_change: {e}")
            
            self.event_bus.publish(EventType.STATE_CHANGED, old_state=old_state, new_state=new_state)
            
        finally:
            # Siempre desmarcar, incluso si hay error
            self._changing_state = False
    
    @property
    def current_state(self) -> SystemState:
        """Retorna estado actual."""
        return self._state
    
    def force_reset(self):
        """Reinicio de emergencia a estado inicial."""
        logger.warning("Reinicio forzado de máquina de estados")
        self._state = SystemState.VERIFICATION
        
        # Resetear todos los servicios
        for svc in self.services.values():
            if hasattr(svc, 'reset'):
                svc.reset()
        
        with self.ctx.write() as ctx:
            ctx.clear_all()
        
        if self.on_state_change:
            self.on_state_change(None, SystemState.VERIFICATION)