# main.py
"""
Punto de entrada principal del sistema de autoguiado.
Wiring de toda la arquitectura — IMX477 + ZEQ25 via ST4/GPIO.
"""

import sys
import threading
import logging

# Configurar logging PRIMERO
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
)
logger = logging.getLogger("Main")
logging.getLogger("CalibrationService").setLevel(logging.DEBUG)
logging.getLogger("GuidingService").setLevel(logging.DEBUG)
logging.getLogger("StateMachine").setLevel(logging.DEBUG)
logging.getLogger("TCPClient").setLevel(logging.DEBUG)
logging.getLogger("Connection_manager").setLevel(logging.DEBUG)

# ─── Configuración ────────────────────────────────────────────────────────────
from config import CONFIG

# ─── Infraestructura transversal ──────────────────────────────────────────────
from shared import ContextManager, SystemContext, EventBus, EventType

# ─── Value objects del dominio ────────────────────────────────────────────────
from domain import PixelScale, FocalLength, ExposureTime, Gain

# ─── Capa de infraestructura ──────────────────────────────────────────────────
from infrastructure import ConnectionManager, ConnectionState
from infrastructure.camera_adapter import CameraAdapter 

# ─── Servicios de aplicación ──────────────────────────────────────────────────
from application import (
    StateMachine,
    SystemState,
    StarSelectionService,
    CalibrationService,
    GuidingService
)

# ─── UI ───────────────────────────────────────────────────────────────────────
from interface import MainWindow, StateRenderer, UIEventAdapter
from interface.adapters import UICommand, CommandEvent


class Application:
    """
    Clase principal que orquesta todo el sistema.
    Implementa el patrón Composition Root.
    """

    def __init__(self):
        logger.info("Inicializando aplicación...")

        # ── 1. Infraestructura transversal ────────────────────────────────────
        self.event_bus = EventBus()
        self.context   = ContextManager(SystemContext())

        # ── 2. Infraestructura de hardware ────────────────────────────────────
        self.connection_manager = ConnectionManager()

        # ── 3. Adaptador que se subscribe para los frames en calibracion y guiado ────────
        camera_adapter = CameraAdapter(self.connection_manager.image_stream)

        # ── 4. Servicios de aplicación ────────────────────────────────────────
        # CalibrationService y GuidingService reciben los adaptadores de
        # hardware directamente para poder llamar capture_single_frame()
        # y guide()/calibrate_pulse() desde sus threads internos.

        calibration_svc = CalibrationService(
            context_manager             = self.context,
            event_bus                   = self.event_bus,
            camera_adapter              = camera_adapter,
            telescope_adapter           = self.connection_manager.st4,
            calibration_pulse_ms        = CONFIG.CALIBRATION.CALIBRATION_PULSE_MS,
            backlash_wait_seconds       = CONFIG.CALIBRATION.BACKLASH_WAIT_SECONDS,
            settle_frames               = CONFIG.CALIBRATION.CENTROID_SETTLE_FRAMES,
            settle_threshold_px         = CONFIG.CALIBRATION.CENTROID_STABLE_THRESHOLD_PX,
            frame_settle_margin_seconds = CONFIG.CALIBRATION.FRAME_SETTLE_MARGIN_SECONDS,
        )
        guiding_svc = GuidingService(
            context_manager          = self.context,
            event_bus                = self.event_bus,
            camera_adapter           = camera_adapter,
            telescope_adapter        = self.connection_manager.st4,
            guide_frequency_hz       = CONFIG.GUIDING.GUIDE_FREQUENCY_HZ,
            rms_window_samples       = CONFIG.GUIDING.RMS_WINDOW_SAMPLES,
            max_pulse_exposure_ratio = CONFIG.GUIDING.MAX_PULSE_EXPOSURE_RATIO,
            max_guide_pulse_ms       = CONFIG.GUIDING.MAX_GUIDE_PULSE_MS,
        )

        # Guardar referencias directas para inyección de M_inv en
        # _on_calibration_complete (igual que phd2_main)
        self._calibration_svc = calibration_svc
        self._guiding_svc     = guiding_svc

        self.services = {
            'star_selection': StarSelectionService(self.context, self.event_bus),
            'calibration':    calibration_svc,
            'guiding':        guiding_svc,
        }

        # ── 4. Máquina de estados ─────────────────────────────────────────────
        self.state_machine = StateMachine(
            self.context,
            self.services,
            self.event_bus
        )

        # ── 5. UI ─────────────────────────────────────────────────────────────
        self.window         = MainWindow()
        self.state_renderer = StateRenderer(self.window)
        self.ui_adapter     = UIEventAdapter(
            self.state_renderer,
            self._handle_command,
            self.services['star_selection']
        )
        self.ui_adapter.bind_to_window(self.window)

        # ── 6. Event handlers ─────────────────────────────────────────────────
        self._setup_event_handlers()
        self.state_machine.on_state_change = self._on_state_change
        self._star_options_map = {}

        logger.info("Aplicación inicializada correctamente")

    # =========================================================================
    # SETUP DE EVENT HANDLERS
    # =========================================================================

    def _setup_event_handlers(self):
        """Conecta eventos del bus a métodos de la UI."""
        self.event_bus.subscribe(EventType.STARS_DETECTED,       self._on_stars_detected)
        self.event_bus.subscribe(EventType.STAR_SELECTED,        self._on_star_selected)
        self.event_bus.subscribe(EventType.CALIBRATION_PROGRESS, self._on_calibration_progress)
        self.event_bus.subscribe(EventType.CALIBRATION_COMPLETE, self._on_calibration_complete)
        self.event_bus.subscribe(EventType.GUIDING_ERROR,        self._on_guiding_error)
        self.event_bus.subscribe(EventType.GUIDING_STATS,        self._on_guiding_stats)
        self.event_bus.subscribe(EventType.CONNECTION_STATUS,    self._on_connection_status)
        self.event_bus.subscribe(EventType.ERROR_OCCURRED,       self._on_error_occurred)
        self.event_bus.start()

    # =========================================================================
    # HANDLERS DE EVENTOS
    # =========================================================================

    def _on_stars_detected(self, event):
        """Muestra lista de estrellas en Panel Manager."""
        # Si ya hay estrella seleccionada, no reabrir el panel.
        # Evita que el selector aparezca constantemente durante calibración/guiado.
        with self.context.read() as ctx:
            if ctx.star.num_estrella > 0:
                return

        stars = event.data.get('stars', [])
        if not stars:
            return

        options = [f"{s['id']} (SNR: {s['snr']:.1f})" for s in stars]
        self._star_options_map = {opt: s['id'] for opt, s in zip(options, stars)}

    def _on_star_selected_from_ui(self, selected_str: str):
        """Callback cuando usuario selecciona estrella en UI."""
        star_id = self._star_options_map.get(selected_str, 0)
        if star_id > 0:
            self.services['star_selection'].select_star(star_id)

    def _on_star_selected(self, event):
        """Confirma selección en status bar."""
        star_id = event.data.get('star_id')
        snr     = event.data.get('snr', 0)
        self.window.set_status_message(
            f"Estrella {star_id} seleccionada (SNR: {snr:.1f})"
        )

    def _on_calibration_progress(self, event):
        """Actualiza progreso de calibración."""
        progress = event.data.get('progress', 0)
        centroid = event.data.get('centroid')
        
        self.window.set_status_message(
            f"Calibrando... {int(progress * 100)}%",
            color="#661111"
        )
        
        # [NUEVO] Dibujar recuadro si hay centroide
        if centroid is not None:
            print(f"[DEBUG] Dibujando centroide: {centroid}")
            self.window.draw_centroid_overlay(centroid)

    def _on_calibration_complete(self, event):
        """
        Inyecta M_inv en GuidingService y muestra resultados en UI.
        CRÍTICO: sin esta inyección el guiado no tiene calibración.
        """
        # Inyectar matriz de calibración en GuidingService
        matrices = self._calibration_svc.get_calibration_matrices()
        if matrices is not None:
            _, m_inv_matrix = matrices  # (M, M_inv)
            self._guiding_svc.set_calibration(m_inv_matrix)
            logger.info("M_inv inyectada en GuidingService tras calibración")
        else:
            logger.error("Calibración completó pero M_inv es None")

        # Actualizar viñeta izquierda con parámetros del sistema
        result = event.data.get('result', {})
        self.window.update_system_params(
            px_scale  = result.get('px_scale', 0.0),
            angle     = result.get('camera_angle', 0.0),
            vel_ra    = result.get('vel_ra', 0.0),
            vel_dec   = result.get('vel_dec', 0.0),
            steps     = result.get('ra_steps', 0),
            ort_error = result.get('ort_error', 0.0)
        )
        self.window.set_status_message("Calibración completada", color="#116611")

    def _on_guiding_error(self, event):
        ra    = event.data.get('ra', 0.0)
        dec   = event.data.get('dec', 0.0)
        total = event.data.get('total', 0.0)
        
        # [NUEVO] Dibujar recuadro si hay centroide
        centroid = event.data.get('centroid')
        if centroid is not None:
            self.window.draw_centroid_overlay(centroid)
        
        self.window.update_error_display(
            ra_px=0.0,   ra_arc=ra,
            dec_px=0.0,  dec_arc=dec,
            tot_px=0.0,  tot_arc=total,
            osc=0
        )
        self.window.add_error_point(ra, dec)

    def _on_guiding_stats(self, event):
        """Actualiza viñeta derecha de errores RMS."""
        d = event.data
        self.window.update_error_display(
            ra_px   = d.get('ra_rms_px',    0.0),
            ra_arc  = d.get('ra_rms_arc',   0.0),
            dec_px  = d.get('dec_rms_px',   0.0),
            dec_arc = d.get('dec_rms_arc',  0.0),
            tot_px  = d.get('total_rms_px', 0.0),
            tot_arc = d.get('total_rms_arc',0.0),
            osc     = d.get('oscillations', 0)
        )

    def _on_connection_status(self, event):
        """Actualiza estado de conexión."""
        status = event.data.get('status')
        if status == ConnectionState.CONNECTED:
            self.context.set_connection_status("connected")
        logger.info(f"Estado de conexión: {status}")

    def _on_error_occurred(self, event):
        """Muestra errores del sistema en la UI."""
        message = event.data.get('message', 'Error desconocido')
        logger.error(f"Error del sistema: {message}")
        self.window.show_warning(message)

    # =========================================================================
    # COMANDOS DE UI
    # =========================================================================

    def _handle_command(self, command_event: CommandEvent):
        """Recibe comandos validados de la UI y los ejecuta."""
        cmd  = command_event.command
        data = command_event.data or {}

        logger.debug(f"Comando recibido: {cmd.name}")

        if cmd == UICommand.LOOP_BUTTON:
            with self.context.write() as ctx:
                ctx.flags.loop_button_clicked = True

        elif cmd == UICommand.CALIBRATE_BUTTON:
            with self.context.write() as ctx:
                ctx.flags.calibrate_button_clicked = True

        elif cmd == UICommand.START_BUTTON:
            with self.context.write() as ctx:
                ctx.flags.start_button_clicked = True

        elif cmd == UICommand.STOP_BUTTON:
            with self.context.write() as ctx:
                ctx.flags.stop_button_clicked = True

        elif cmd == UICommand.SET_PX_SCALE:
            value = data.get('value', 0)
            try:
                # Validar que el tamaño de píxel sea razonable (0.1 a 20 µm)
                if value < 0.1 or value > 20.0:
                    raise ValueError("Tamaño de píxel debe estar entre 0.1 y 20 µm")
                
                # Guardar tamaño de píxel (la escala se calcula automáticamente)
                self.context.update_user_config(pixel_size=value)
                
                # Mostrar ambos valores al usuario
                with self.context.read() as ctx:
                    calculated_scale = ctx.user.px_scale
                self.window.set_status_message(
                    f"Tamaño píxel: {value} µm | Escala calculada: {calculated_scale:.2f} arcsec/px"
                )
            except ValueError as e:
                self.window.show_warning(str(e))

        elif cmd == UICommand.SET_FOCAL_DIST:
            value = data.get('value', 0)
            try:
                fl = FocalLength(value)
                self.context.update_user_config(focal=float(fl))
            except ValueError as e:
                self.window.show_warning(str(e))

        elif cmd == UICommand.SET_EXPOSURE:
            # data['value'] = segundos (float), data['exposure_us'] = microsegundos (int)
            seconds     = data.get('value', 1.0)
            exposure_us = data.get('exposure_us', int(seconds * 1_000_000))

            try:
                exp = ExposureTime(seconds)
                seconds_float = float(exp)
                exposure_us   = int(seconds_float * 1_000_000)
                exposure_us   = max(CONFIG.CAMERA.MIN_EXPOSURE_US,
                                    min(CONFIG.CAMERA.MAX_EXPOSURE_US, exposure_us))

                # Guardar en SEGUNDOS — los servicios de calibración y guiado
                # leen ctx.camera.expo_time en segundos
                with self.context.write() as ctx:
                    ctx.camera.expo_time = seconds_float

                # Enviar a hardware en microsegundos
                self.connection_manager.send_camera_settings(
                    exposure_us = exposure_us,
                    gain_raw    = self._get_current_gain()
                )
                self.window.set_status_message(
                    f"Exposición: {seconds_float}s ({exposure_us}µs)"
                )
            except ValueError as e:
                self.window.show_warning(str(e))

        elif cmd == UICommand.SET_GAIN:
            # data['value'] = valor UI ×10 (10-160), data['gain_raw'] = valor real (1.0-16.0)
            gain_value = data.get('value', 10)
            gain_raw   = data.get('gain_raw',
                                  gain_value / CONFIG.CAMERA.GAIN_MULTIPLIER)
            try:
                with self.context.write() as ctx:
                    ctx.camera.gain = int(gain_value)

                self.connection_manager.send_camera_settings(
                    exposure_us = self._get_current_exposure_us(),
                    gain_raw    = float(gain_raw)
                )
                self.window.set_status_message(f"Ganancia: {gain_raw:.1f}x")
            except ValueError as e:
                self.window.show_warning(str(e))

        elif cmd == UICommand.SET_STAR_NUMBER:
            pass  # Manejado en _on_star_selected_from_ui

        elif cmd == UICommand.SET_X_SCALE:
            value = data.get('value', 100)
            with self.context.write() as ctx:
                ctx.user.graph_x_scale = int(value)
            self._update_window_graph_state()
            self.window.set_status_message(f"Escala X: {int(value)} muestras")

        elif cmd == UICommand.SET_Y_SCALE:
            value = data.get('value', 5.0)
            with self.context.write() as ctx:
                ctx.user.graph_y_scale = float(value)
            self._update_window_graph_state()
            self.window.set_status_message(f"Escala Y: ±{float(value)} arcsec")

        elif cmd == UICommand.SET_AGGRESSIVITY:
            value = data.get('value', 70)
            with self.context.write() as ctx:
                ctx.user.aggressiveness = float(value)
            self.window.set_status_message(f"Agresividad: {float(value)}%")

        elif cmd == UICommand.SET_MIN_MO:
            value = data.get('value', 0.2)
            with self.context.write() as ctx:
                ctx.user.min_mo = float(value)
            self.window.set_status_message(f"MinMo: {float(value)} arcsec")

    # =========================================================================
    # CAMBIOS DE ESTADO
    # =========================================================================

    def _on_state_change(self, old_state, new_state):
        """Callback cuando la máquina de estados cambia de estado."""
        logger.info(f"UI actualizando a estado: {new_state.name}")

        self.state_renderer.render(new_state)

        with self.context.write() as ctx:
            ctx.state_number = new_state.value

        if new_state == SystemState.STAR_SELECTION:
            # Iniciar detección y suscribir stream con anotaciones
            self.services['star_selection'].start_detection()
            self.connection_manager.subscribe_to_images(
                self._on_image_for_star_selection
            )
            # Vincular estado del gráfico para escalas X/Y
            with self.context.read() as ctx:
                graph_state = type('obj', (object,), {
                    'Xscale': ctx.user.graph_x_scale,
                    'Yscale': ctx.user.graph_y_scale
                })()
            self.window.bind_graph_state(graph_state)

        elif old_state == SystemState.STAR_SELECTION:
            # Detener detección al salir del estado
            self.services['star_selection'].stop_detection()
            self.connection_manager.image_stream.unsubscribe(self._on_image_for_star_selection)
            self.connection_manager.subscribe_to_images(self.window.update_camera_image)

    def _on_image_for_star_selection(self, image):
        """
        Handler especial para estado STAR_SELECTION.
        Pasa el frame al servicio para detección Y muestra imagen anotada.
        """
        # Primero: pasar el frame al servicio para que procese detecciones
        self.services['star_selection'].on_image_received(image)
        # Segundo: mostrar imagen con recuadros de estrellas detectadas
        annotated = self.services['star_selection'].get_annotated_image(image)
        self.window.update_camera_image(annotated)

    def _update_window_graph_state(self):
        """Actualiza el estado del gráfico con valores actuales del contexto."""
        with self.context.read() as ctx:
            graph_state = type('obj', (object,), {
                'Xscale': ctx.user.graph_x_scale,
                'Yscale': ctx.user.graph_y_scale
            })()
        self.window.bind_graph_state(graph_state)

    # =========================================================================
    # UTILIDADES
    # =========================================================================

    def _get_current_exposure_us(self) -> int:
        """Retorna exposición actual en microsegundos (para send_camera_settings)."""
        with self.context.read() as ctx:
            return int(ctx.camera.expo_time * 1_000_000)

    def _get_current_gain(self) -> float:
        """Retorna ganancia actual en valor real (1.0-16.0)."""
        with self.context.read() as ctx:
            return ctx.camera.gain / CONFIG.CAMERA.GAIN_MULTIPLIER

    # =========================================================================
    # CICLO DE VIDA
    # =========================================================================

    def run(self):
        """Inicia la aplicación."""
        logger.info("Iniciando sistema...")

        # Iniciar máquina de estados
        self.state_machine.start()

        # Iniciar conexión hardware en thread separado (no bloquea la UI)
        self._start_connection_thread()

        # Iniciar UI (bloqueante — hilo principal)
        try:
            self.window.mainloop()
        except KeyboardInterrupt:
            logger.info("Interrupción de teclado")
        finally:
            self.shutdown()

    def _start_connection_thread(self):
        """Conecta con la Pi en thread separado."""
        def connect():
            if self.connection_manager.connect():
                with self.context.write() as ctx:
                    ctx.connection_status = "connected"

                self.event_bus.publish(
                    EventType.CONNECTION_STATUS,
                    status=ConnectionState.CONNECTED
                )

                # Suscribir visualización básica hasta entrar a STAR_SELECTION
                self.connection_manager.subscribe_to_images(
                    self.window.update_camera_image
                )
                logger.info("Conexión con Pi establecida")
            else:
                with self.context.write() as ctx:
                    ctx.connection_status = "error"
                logger.error(
                    "Falló la conexión con la Pi. "
                    "Verifica que el servidor está corriendo y el cable USB conectado."
                )

        threading.Thread(target=connect, daemon=True).start()

    def shutdown(self):
        """Limpieza ordenada al cerrar."""
        logger.info("Cerrando aplicación...")

        self.state_machine.stop()
        self.services['star_selection'].stop_detection()
        self._guiding_svc.stop_guiding()
        self.event_bus.stop()
        self.connection_manager.disconnect()
        self.window._safe_close()

        logger.info("Aplicación cerrada")
        sys.exit(0)


def main():
    """Punto de entrada."""
    app = Application()
    app.run()


if __name__ == "__main__":
    main()