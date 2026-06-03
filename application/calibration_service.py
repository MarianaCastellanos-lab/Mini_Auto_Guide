# application/calibration_service.py
"""
Servicio de calibración — cliente real (IMX477 + ZEQ25 via ST4/GPIO).

Algoritmo:
  1. Capturar posición de referencia estabilizada (x0, y0).
  2. Enviar pulso RA+ de duración fija (calibration_pulse_ms).
     En hardware real, calibrate_pulse() es bloqueante (GPIO).
  3. Esperar post_pulse_wait = exposure_s + cal_pulse_s + margin
     para garantizar que el siguiente frame fue capturado DESPUÉS
     del movimiento de la montura (no un frame del buffer anterior).
  4. Medir centroide post-RA+ estabilizado (x1, y1).
     vec_RA = ((x1-x0)/ms, (y1-y0)/ms)
  5. Esperar backlash_wait_seconds.
  6. Capturar posición pre-DEC estabilizada (xd0, yd0) — FIX A.
  7. Enviar pulso DEC+, esperar post_pulse_wait.
  8. Medir centroide post-DEC+ estabilizado (x2, y2).
     vec_DEC = ((x2-xd0)/ms, (y2-yd0)/ms)  ← usa ref pre-DEC, no (x0,y0)
  9. Construir matriz M 2x2, calcular M_inv, guardar en contexto.

FIX A — referencia pre-DEC:
  La montura real puede no volver a (x0,y0) tras el pulso RA (histéresis,
  backlash). Usar (x0,y0) como referencia para vec_DEC introduciría el
  desplazamiento residual de RA en vec_DEC → ort_error alto (~42° en
  el simulador antes del fix). La solución es capturar la posición real
  de la estrella justo antes del pulso DEC y usarla como referencia.

Esperas post-pulso:
  post_pulse_wait  = exposure_s + cal_pulse_s + frame_settle_margin
  inter_frame_wait = exposure_s + frame_settle_margin

  Con frame_settle_margin=0.0 (default hardware real):
    post_pulse_wait  = exposure_s + cal_pulse_s
    inter_frame_wait = exposure_s

  Ambas esperas son necesarias incluso con GPIO bloqueante:
  el pulso termina pero la cámara necesita completar una exposición
  NUEVA para que el frame refleje la nueva posición de la estrella.
  inter_frame_wait garantiza que _capture_stable_centroid() no lee
  el mismo frame en intentos consecutivos.

Interfaz pública idéntica al simulador:
  start_calibration()
  is_complete() -> bool
  get_progress() -> float
  get_result() -> dict | None
  reset()
  get_calibration_matrices() -> Optional[Tuple[np.ndarray, np.ndarray]]
"""

import threading
import time
import logging
import numpy as np
from typing import Optional, List, Tuple
from dataclasses import dataclass

from shared.event_bus import EventType, EventBus
from shared.context_manager import ContextManager

logger = logging.getLogger("CalibrationService")


@dataclass
class CalibrationResult:
    """
    Resultados de calibración.
    Campos compatibles con CalibrationProfile del contexto y con
    lo que necesita GuidingService via get_calibration_matrices().
    """
    px_scale: float       # Del contexto (ingresado por el usuario)
    camera_angle: float   # Ángulo de la cámara respecto a RA (grados)
    vel_ra: float         # Velocidad RA en px/ms
    vel_dec: float        # Velocidad DEC en px/ms
    ra_steps: int         # Frames usados para estabilizar centroide RA
    ort_error: float      # Error de ortogonalidad entre vec_RA y vec_DEC (grados)
    # Internos — usados por GuidingService
    m_matrix: np.ndarray  # Matriz 2x2: columnas = vec_RA, vec_DEC
    m_inv: np.ndarray     # Inversa de m_matrix


class CalibrationService:
    """
    Servicio de calibración real.
    Mueve la montura ZEQ25 via ST4Gateway y mide desplazamientos
    en frames de la cámara IMX477.
    """

    _PROGRESS_STEPS = {
        'inicio':            0.05,
        'ref_capturada':     0.15,
        'pulso_ra_enviado':  0.30,
        'ra_medido':         0.50,
        'espera_backlash':   0.60,
        'pulso_dec_enviado': 0.75,
        'dec_medido':        0.90,
        'completo':          1.00,
    }

    def __init__(self, context_manager: ContextManager, event_bus: EventBus,
                 camera_adapter, telescope_adapter,
                 calibration_pulse_ms: int,
                 backlash_wait_seconds: float,
                 settle_frames: int,
                 settle_threshold_px: float,
                 frame_settle_margin_seconds: float = 0.0):
        """
        Args:
            context_manager:             ContextManager compartido.
            event_bus:                   EventBus compartido.
            camera_adapter:              Adaptador de cámara con
                                         capture_single_frame() bloqueante.
            telescope_adapter:           ST4Gateway con calibrate_pulse()
                                         bloqueante (GPIO).
            calibration_pulse_ms:        Duración del pulso de calibración (ms).
                                         Recomendado: 1500ms para ZEQ25.
            backlash_wait_seconds:       Segundos de espera entre pulso RA y
                                         captura pre-DEC. Permite absorber
                                         backlash mecánico de la montura.
            settle_frames:               Frames consecutivos estables requeridos
                                         para aceptar un centroide.
            settle_threshold_px:         Variación máxima en px entre frames
                                         para considerar el centroide "estable".
            frame_settle_margin_seconds: Margen adicional añadido a exposure_s
                                         en post_pulse_wait e inter_frame_wait.
                                         Default 0.0 para hardware real.
        """
        self.ctx = context_manager
        self.event_bus = event_bus
        self._camera = camera_adapter
        self._telescope = telescope_adapter
        self._cal_pulse_ms = calibration_pulse_ms
        self._backlash_wait = backlash_wait_seconds
        self._settle_frames = settle_frames
        self._settle_threshold = settle_threshold_px
        self._frame_settle_margin = frame_settle_margin_seconds

        self._calibrating = False
        self._complete = False
        self._progress = 0.0
        self._result: Optional[CalibrationResult] = None
        self._thread: Optional[threading.Thread] = None

        from application.image_processor import ImageProcessor

        self._processor = ImageProcessor(
            sigma_k=3.0,
            min_pixels=3,
            gaussian_sigma=1.0,
            snr_aperture=5,
            min_snr=3.0
        )
    # =========================================================================
    # INTERFAZ PÚBLICA
    # =========================================================================

    def start_calibration(self):
        """Inicia calibración en thread separado."""
        if self._calibrating:
            return

        self._calibrating = True
        self._complete = False
        self._progress = 0.0
        self._result = None

        self._thread = threading.Thread(
            target=self._calibration_loop,
            daemon=True,
            name="Calibration"
        )
        self._thread.start()

        logger.info("Calibración iniciada")
        self.event_bus.publish(EventType.CALIBRATION_STARTED)

    def is_complete(self) -> bool:
        return self._complete

    def get_progress(self) -> float:
        return self._progress

    def get_result(self) -> Optional[dict]:
        """Retorna resultado formateado para UI."""
        if not self._complete or self._result is None:
            return None
        return self._format_result_for_ui(self._result)

    def reset(self):
        self._calibrating = False
        self._complete = False
        self._progress = 0.0
        self._result = None

    def get_calibration_matrices(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """
        Retorna (M, M_inv) para que GuidingService las use.
        Llamado desde main.py después de CALIBRATION_COMPLETE.
        """
        if self._result is None:
            return None
        return (self._result.m_matrix, self._result.m_inv)

    # =========================================================================
    # LOOP PRINCIPAL DE CALIBRACIÓN
    # =========================================================================

    def _calibration_loop(self):
        """Ejecuta el algoritmo. Cualquier excepción aborta y reporta."""
        try:
            self._run_calibration()
        except Exception as e:
            logger.error(f"Calibración abortada por error: {e}", exc_info=True)
            self._calibrating = False
            self.event_bus.publish(
                EventType.ERROR_OCCURRED,
                message=f"Error de calibración: {e}"
            )

    def _run_calibration(self):
        """Secuencia completa de calibración."""

        # Leer tiempo de exposición actual del contexto.
        # El usuario puede haberlo modificado desde la UI antes de calibrar.
        with self.ctx.read() as ctx:
            exposure_s = ctx.camera.expo_time  # segundos

        # post_pulse_wait: tiempo a esperar DESPUÉS de que el pulso termina,
        # antes de capturar el centroide post-pulso.
        # Necesario incluso con GPIO bloqueante: el pulso termina pero la
        # cámara necesita completar una exposición completa para que el
        # frame refleje la nueva posición de la estrella.
        # Fórmula: exposure_s + cal_pulse_s + margin
        cal_pulse_s     = self._cal_pulse_ms / 1000.0
        post_pulse_wait = exposure_s + cal_pulse_s + self._frame_settle_margin

        # inter_frame_wait: tiempo a esperar ENTRE intentos consecutivos de
        # _capture_stable_centroid. Sin esta espera, intentos sucesivos
        # pueden leer el mismo frame → spread=0 artificial → centroide
        # aceptado inmediatamente aunque sea incorrecto.
        inter_frame_wait = exposure_s + self._frame_settle_margin

        logger.info(
            f"[calibración] exposure={exposure_s:.2f}s, "
            f"cal_pulse={cal_pulse_s:.2f}s, "
            f"margin={self._frame_settle_margin:.1f}s → "
            f"post_pulse_wait={post_pulse_wait:.2f}s, "
            f"inter_frame_wait={inter_frame_wait:.2f}s"
        )

        # --- Paso 1: capturar posición de referencia ---
        self._report_progress('inicio', "Capturando posición de referencia...")
        ref_centroid = self._capture_stable_centroid("referencia", inter_frame_wait)
        if ref_centroid is None:
            raise RuntimeError(
                "No se encontró estrella para calibrar. "
                "Verifique exposición y selección de estrella."
            )

        x0, y0 = ref_centroid
        logger.info(f"Centroide de referencia: ({x0:.2f}, {y0:.2f})")
        self._report_progress('ref_capturada', f"Ref: ({x0:.1f}, {y0:.1f})")

        # --- Paso 2: pulso RA+ ---
        logger.info(f"Enviando pulso RA+ ({self._cal_pulse_ms} ms)...")
        self._report_progress('pulso_ra_enviado', "Pulso RA+ enviado...")
        self._telescope.calibrate_pulse("RA+")
        # calibrate_pulse() es bloqueante en hardware real (GPIO).
        # El pulso físico ya terminó al retornar. Aun así, esperamos
        # post_pulse_wait para que la cámara complete una exposición
        # nueva DESPUÉS del movimiento.
        logger.info(f"[post-RA+] Esperando frame fresco ({post_pulse_wait:.2f}s)...")
        time.sleep(post_pulse_wait)

        ra_centroid = self._capture_stable_centroid("post-RA+", inter_frame_wait)
        if ra_centroid is None:
            raise RuntimeError("No se pudo medir centroide después de pulso RA+.")

        x1, y1 = ra_centroid
        logger.info(f"Centroide post-RA+: ({x1:.2f}, {y1:.2f})")
        self._report_progress('ra_medido', f"RA medido: Δ({x1-x0:.1f}, {y1-y0:.1f})")

        # Vector RA en píxeles por milisegundo
        vec_ra = np.array([(x1 - x0) / self._cal_pulse_ms,
                           (y1 - y0) / self._cal_pulse_ms])
        logger.info(f"Vector RA: {vec_ra} px/ms")

        # --- Paso 3: esperar backlash + capturar referencia pre-DEC (FIX A) ---
        # La montura puede no volver a (x0,y0) tras el pulso RA (histéresis,
        # backlash). Si usáramos (x0,y0) como referencia para vec_DEC, el
        # desplazamiento residual del pulso RA contaminaría vec_DEC.
        # Solución: capturar la posición REAL justo antes del pulso DEC.
        logger.info(f"Esperando backlash ({self._backlash_wait}s)...")
        self._report_progress('espera_backlash',
                              f"Esperando backlash ({self._backlash_wait}s)...")
        time.sleep(self._backlash_wait)

        logger.info("Capturando posición pre-DEC (referencia para vec_DEC)...")
        pre_dec_centroid = self._capture_stable_centroid("pre-DEC", inter_frame_wait)
        if pre_dec_centroid is None:
            logger.warning("No se obtuvo centroide pre-DEC, usando post-RA+ como referencia")
            pre_dec_centroid = (x1, y1)

        xd0, yd0 = pre_dec_centroid
        logger.info(
            f"Referencia pre-DEC: ({xd0:.2f}, {yd0:.2f})  "
            f"(desplazamiento residual RA: Δx={xd0-x0:.2f}px, Δy={yd0-y0:.2f}px)"
        )

        # --- Paso 4: pulso DEC+ ---
        logger.info(f"Enviando pulso DEC+ ({self._cal_pulse_ms} ms)...")
        self._report_progress('pulso_dec_enviado', "Pulso DEC+ enviado...")
        self._telescope.calibrate_pulse("DEC+")
        # Mismo patrón: bloqueante en hardware real, pero esperamos
        # post_pulse_wait para garantizar frame fresco.
        logger.info(f"[post-DEC+] Esperando frame fresco ({post_pulse_wait:.2f}s)...")
        time.sleep(post_pulse_wait)

        dec_centroid = self._capture_stable_centroid("post-DEC+", inter_frame_wait)
        if dec_centroid is None:
            raise RuntimeError("No se pudo medir centroide después de pulso DEC+.")

        x2, y2 = dec_centroid
        logger.info(f"Centroide post-DEC+: ({x2:.2f}, {y2:.2f})")
        self._report_progress('dec_medido', f"DEC medido: Δ({x2-xd0:.1f}, {y2-yd0:.1f})")

        # Vector DEC: usar referencia pre-DEC (xd0,yd0), NO (x0,y0).
        vec_dec = np.array([(x2 - xd0) / self._cal_pulse_ms,
                            (y2 - yd0) / self._cal_pulse_ms])
        logger.info(f"Vector DEC: {vec_dec} px/ms")

        # --- Paso 5: construir matriz y calcular resultado ---
        result = self._compute_result(vec_ra, vec_dec)
        self._finish_calibration(result)

    # =========================================================================
    # CAPTURA Y MEDICIÓN DE CENTROIDE
    # =========================================================================

    def _capture_stable_centroid(self, label: str,
                                  inter_frame_wait: float = 0.0
                                  ) -> Optional[Tuple[float, float]]:
        """
        Captura frames hasta obtener settle_frames mediciones consecutivas
        donde el centroide varía menos de settle_threshold_px.

        Args:
            label:            Etiqueta para logs ("referencia", "post-RA+", ...).
            inter_frame_wait: Segundos a esperar entre intentos consecutivos.
                              Calculado como exposure_s + frame_settle_margin.
                              Con frame_settle_margin=0.0 vale exactamente
                              exposure_s: la cámara completa una exposición
                              entre intento e intento, garantizando frames
                              distintos y sin lecturas de buffer repetidas.

        Returns:
            (x, y) del centroide estabilizado (promedio de la ventana),
            o None si no se logra en max_attempts.
        """

        # Leer estrella de referencia del contexto para seleccionar la
        # detección más cercana a la estrella guía en cada frame.
        with self.ctx.read() as ctx:
            ref_star_x = ctx.star.centroid_x
            ref_star_y = ctx.star.centroid_y
            has_ref = ctx.star.num_estrella > 0

        logger.debug(
            f"[{label}] ref_ctx: x={ref_star_x:.2f}, y={ref_star_y:.2f}, "
            f"has_ref={has_ref}"
        )

        history: List[Tuple[float, float]] = []
        max_attempts = self._settle_frames + 20  # Límite de seguridad

        for attempt in range(max_attempts):
            # Esperar entre intentos (excepto el primero) para garantizar
            # que cada capture_single_frame() entrega un frame distinto.
            if inter_frame_wait > 0 and attempt > 0:
                time.sleep(inter_frame_wait)

            frame = self._camera.capture_single_frame()
            if frame is None:
                logger.warning(f"[{label}] Frame nulo en intento {attempt}")
                time.sleep(0.2)
                continue

            detections = self._processor.process(frame)
            if not detections:
                logger.debug(f"[{label}] Sin detecciones en intento {attempt}")
                time.sleep(0.2)
                continue

            # Seleccionar la detección más cercana a la estrella de referencia
            if has_ref:
                detections_sorted = sorted(
                    detections,
                    key=lambda s: (s.x - ref_star_x)**2 + (s.y - ref_star_y)**2
                )
                best = detections_sorted[0]
            else:
                detections_sorted = detections  # ya ordenadas por SNR
                best = detections[0]

            cx, cy = best.x, best.y
            history.append((cx, cy))

            # Log diagnóstico: top-3 detecciones con distancias y SNR
            top3 = detections_sorted[:3]
            top3_str = '  |  '.join(
                f"rank{i}: ({s.x:.1f},{s.y:.1f}) "
                f"d={((s.x-ref_star_x)**2+(s.y-ref_star_y)**2)**0.5:.1f}px "
                f"snr={s.snr:.0f}"
                for i, s in enumerate(top3)
            )
            logger.debug(
                f"[{label}] intento={attempt} n={len(detections)} "
                f"SELECCIONADA=({cx:.2f},{cy:.2f}) "
                f"dist={((cx-ref_star_x)**2+(cy-ref_star_y)**2)**0.5:.2f}px\n"
                f"          top3: {top3_str}"
            )

            # Verificar estabilidad en ventana de settle_frames
            if len(history) >= self._settle_frames:
                window = history[-self._settle_frames:]
                xs = [p[0] for p in window]
                ys = [p[1] for p in window]
                spread = max(max(xs) - min(xs), max(ys) - min(ys))

                if spread < self._settle_threshold:
                    stable_x = float(np.mean(xs))
                    stable_y = float(np.mean(ys))
                    logger.info(
                        f"[{label}] Centroide estable tras {attempt+1} intentos: "
                        f"({stable_x:.2f}, {stable_y:.2f}), spread={spread:.3f}px"
                    )

                    # [NUEVO] Publicar centroide para dibujar recuadro en UI
                    self.event_bus.publish(
                        EventType.CALIBRATION_PROGRESS,
                        progress=self._progress,
                        centroid=(stable_x, stable_y),  # ← NUEVO: centroide detectado
                        label=f"{label}: ({stable_x:.1f}, {stable_y:.1f})"
                    )

                    return (stable_x, stable_y)

        logger.error(
            f"[{label}] No se logró centroide estable en {max_attempts} intentos"
        )
        return None

    # =========================================================================
    # CÁLCULO DE RESULTADOS
    # =========================================================================

    def _compute_result(self, vec_ra: np.ndarray,
                        vec_dec: np.ndarray) -> CalibrationResult:
        """
        Construye la matriz de calibración y calcula parámetros derivados.

        Matriz M:
          M = [[vec_ra[0],  vec_dec[0]],   (componentes X)
               [vec_ra[1],  vec_dec[1]]]   (componentes Y)

        M convierte [ms_RA, ms_DEC] → [Δx, Δy] en píxeles.
        M_inv convierte [Δx, Δy] → [ms_RA, ms_DEC], usado por GuidingService.
        """
        m_matrix = np.array([
            [vec_ra[0], vec_dec[0]],
            [vec_ra[1], vec_dec[1]]
        ])

        det = np.linalg.det(m_matrix)
        if abs(det) < 1e-10:
            raise RuntimeError(
                f"Matriz de calibración singular (det={det:.2e}). "
                "Los vectores RA y DEC son casi paralelos. "
                "Verifique que la montura responde a los pulsos."
            )

        m_inv = np.linalg.inv(m_matrix)

        vel_ra  = float(np.linalg.norm(vec_ra))
        vel_dec = float(np.linalg.norm(vec_dec))

        # Cambiar de px/ms a px/s (multiplicar por 1000)
        vel_ra_s  = vel_ra * 1000.0
        vel_dec_s = vel_dec * 1000.0

        # Ángulo del vector RA respecto al eje X del sensor.
        # Puede ser cualquier valor (-180°, +180°]. Depende de la rotación
        # física de la cámara en el tubo. NO debe ser necesariamente 90°.
        camera_angle = float(np.degrees(np.arctan2(vec_ra[1], vec_ra[0])))

        # Error de ortogonalidad: diferencia respecto a 90° entre RA y DEC.
        # Con FIX A aplicado, debe quedar < 10° para montura bien alineada.
        cos_angle = float(np.dot(vec_ra, vec_dec) /
                          (np.linalg.norm(vec_ra) * np.linalg.norm(vec_dec) + 1e-12))
        cos_angle     = float(np.clip(cos_angle, -1.0, 1.0))
        angle_between = float(np.degrees(np.arccos(cos_angle)))
        ort_error     = abs(90.0 - angle_between)

        with self.ctx.read() as ctx:
            px_scale = ctx.user.px_scale

        logger.info(
            f"Calibración calculada:\n"
            f"  vec_RA  = {vec_ra} px/ms\n"
            f"  vec_DEC = {vec_dec} px/ms\n"
            f"  vel_RA  = {vel_ra:.4f} px/ms\n"
            f"  vel_DEC = {vel_dec:.4f} px/ms\n"
            f"  ángulo cámara = {camera_angle:.2f}°\n"
            f"  error ortog.  = {ort_error:.2f}°\n"
            f"  det(M)        = {det:.4f}"
        )

        return CalibrationResult(
            px_scale=px_scale,
            camera_angle=round(camera_angle, 2),
            vel_ra=round(vel_ra_s, 4),
            vel_dec=round(vel_dec_s, 4),
            ra_steps=self._settle_frames,
            ort_error=round(ort_error, 2),
            m_matrix=m_matrix,
            m_inv=m_inv
        )

    def _finish_calibration(self, result: CalibrationResult):
        """Guarda resultado en contexto y notifica a la UI."""
        self._result = result
        self._calibrating = False
        self._complete = True

        with self.ctx.write() as ctx:
            ctx.calibration.px_scale     = result.px_scale
            ctx.calibration.camera_angle = result.camera_angle
            ctx.calibration.vel_ra       = result.vel_ra
            ctx.calibration.vel_dec      = result.vel_dec
            ctx.calibration.ra_steps     = result.ra_steps
            ctx.calibration.ort_error    = result.ort_error
            # m_matrix y m_inv no van al contexto —
            # GuidingService los obtiene via get_calibration_matrices()

        self._report_progress('completo', "Calibración completa")
        self.event_bus.publish(
            EventType.CALIBRATION_COMPLETE,
            result=self._format_result_for_ui(result)
        )
        logger.info("Calibración completada exitosamente")

    # =========================================================================
    # UTILIDADES
    # =========================================================================

    def _report_progress(self, step: str, message: str, centroid: Optional[tuple] = None):
        """Actualiza progreso y publica evento para la UI."""
        self._progress = self._PROGRESS_STEPS.get(step, self._progress)
        kwargs = {
            'progress': self._progress,
            'message': message
        }
        if centroid is not None:
            kwargs['centroid'] = centroid
            kwargs['label'] = message
        
        self.event_bus.publish(EventType.CALIBRATION_PROGRESS, **kwargs)
        logger.info(f"[CAL {int(self._progress*100):3d}%] {message}")

    def _format_result_for_ui(self, result: CalibrationResult) -> dict:
        """Formato compatible con _on_calibration_complete en main.py."""
        return {
            'px_scale':     result.px_scale,
            'camera_angle': result.camera_angle,
            'vel_ra':       result.vel_ra,
            'vel_dec':      result.vel_dec,
            'ra_steps':     result.ra_steps,
            'ort_error':    result.ort_error,
        }