# application/guiding_service.py
"""
Servicio de guiado proporcional — cliente real (IMX477 + ZEQ25 via ST4/GPIO).

Algoritmo por frame:
  1. Capturar frame y detectar centroide de la estrella guía.
  2. Calcular error: (Δx, Δy) = centroide_actual - centroide_referencia
  3. Convertir a arcosegundos usando px_scale del contexto.
  4. Publicar errores y estadísticas para la UI.
  5. Si |error_total| < MinMo → no corregir (evita correcciones por ruido).
  6. Calcular pulso necesario: [ms_RA, ms_DEC] = M_inv × [Δx, Δy]
  7. Aplicar agresividad: pulso_final = pulso_necesario × (agresividad / 100)
  8. Limitar cada pulso a min(MAX_PULSE_MS, exposure_ms × max_ratio).
  9. Enviar pulsos RA y DEC (secuenciales — ST4/GPIO es bloqueante).
  10. Esperar al siguiente ciclo según guide_frequency_hz.

Diferencias respecto al simulador PHD2:
  - Los pulsos ST4 son BLOQUEANTES (GPIO). Se envían RA primero, luego DEC.
    En el simulador eran no bloqueantes y se consideraban "simultáneos".
  - La detección de frame stale (hash) se mantiene como protección defensiva,
    aunque con hardware real capture_single_frame() siempre entrega frames nuevos.

GuidingService necesita:
  - La matriz M_inv (de CalibrationService via main.py, método set_calibration())
  - El centroide de referencia (del contexto, establecido en el primer frame)
  - camera_adapter con capture_single_frame() bloqueante
  - telescope_adapter (ST4Gateway) con guide() bloqueante
"""

import threading
import time
import logging
import math
import numpy as np
from typing import Optional
from collections import deque
from dataclasses import dataclass

from shared.event_bus import EventType, EventBus
from shared.context_manager import ContextManager

logger = logging.getLogger("GuidingService")


@dataclass
class GuideError:
    """Error de guiado en un instante."""
    ra_arcsec:    float
    dec_arcsec:   float
    ra_pixels:    float
    dec_pixels:   float
    total_arcsec: float
    total_pixels: float


class GuidingService:
    """
    Servicio de guiado en lazo cerrado.
    Usa la matriz de calibración M_inv para convertir error en píxeles
    a pulsos de corrección ST4.
    """

    def __init__(self, context_manager: ContextManager, event_bus: EventBus,
                 camera_adapter, telescope_adapter,
                 guide_frequency_hz: float, rms_window_samples: int,
                 max_pulse_exposure_ratio: float, max_guide_pulse_ms: int):
        """
        Args:
            context_manager:          ContextManager compartido.
            event_bus:                EventBus compartido.
            camera_adapter:           Adaptador de cámara con
                                      capture_single_frame() bloqueante.
            telescope_adapter:        ST4Gateway con guide() bloqueante.
            guide_frequency_hz:       Frecuencia del loop de guiado (Hz).
            rms_window_samples:       Tamaño de ventana para cálculo de RMS.
            max_pulse_exposure_ratio: Fracción máxima de exposición para pulso.
                                      Evita pulsos más largos que la exposición.
            max_guide_pulse_ms:       Límite absoluto de pulso en ms.
        """
        self.ctx = context_manager
        self.event_bus = event_bus
        self._camera = camera_adapter
        self._telescope = telescope_adapter
        self._guide_frequency = guide_frequency_hz
        self._rms_window = rms_window_samples
        self._max_ratio = max_pulse_exposure_ratio
        self._max_pulse_ms = max_guide_pulse_ms

        self._guiding = False
        self._thread: Optional[threading.Thread] = None

        # M_inv se inyecta desde main.py después de la calibración
        self._m_inv: Optional[np.ndarray] = None

        # Centroide de referencia (posición en el primer frame de guiado)
        self._ref_x: Optional[float] = None
        self._ref_y: Optional[float] = None

        # Hash del último frame procesado. Protección defensiva: aunque el
        # hardware real siempre entrega frames nuevos, evita procesar el
        # mismo frame dos veces si capture_single_frame() retornase stale
        # por alguna condición inesperada.
        self._last_frame_hash: Optional[int] = None

        # Historial para RMS
        self._error_history: deque = deque(maxlen=rms_window_samples)

        # Contador de oscilaciones RA (cambios de signo)
        self._ra_oscillations = 0
        self._last_ra_sign = 0

        from application.image_processor import ImageProcessor

        self._processor = ImageProcessor(
            sigma_k=3.0,
            min_pixels=3,
            gaussian_sigma=1.0,
            snr_aperture=5,
            min_snr=3.0
        )

    # =========================================================================
    # INYECCIÓN DE CALIBRACIÓN — llamado desde main.py
    # =========================================================================

    def set_calibration(self, m_inv: np.ndarray):
        """
        Inyecta la matriz inversa de calibración.
        Llamado por main.py después de CALIBRATION_COMPLETE.
        main.py obtiene m_inv de CalibrationService.get_calibration_matrices().
        """
        self._m_inv = m_inv
        logger.info("Matriz de calibración M_inv cargada en GuidingService")

    # =========================================================================
    # INTERFAZ PÚBLICA
    # =========================================================================

    def start_guiding(self):
        """Inicia guiado en thread separado."""
        if self._guiding:
            return

        if self._m_inv is None:
            logger.error("No hay calibración disponible. "
                         "Ejecute calibración antes de guiar.")
            self.event_bus.publish(
                EventType.ERROR_OCCURRED,
                message="Sin calibración. Ejecute calibración primero."
            )
            return

        self._guiding = True
        self._error_history.clear()
        self._ra_oscillations = 0
        self._last_ra_sign = 0
        self._ref_x = None  # Se establece en el primer frame
        self._ref_y = None
        self._last_frame_hash = None

        self._thread = threading.Thread(
            target=self._guiding_loop,
            daemon=True,
            name="Guiding"
        )
        self._thread.start()

        logger.info("Guiado iniciado")
        self.event_bus.publish(EventType.GUIDING_STARTED)

    def stop_guiding(self):
        """Detiene guiado."""
        self._guiding = False
        logger.info("Guiado detenido")
        self.event_bus.publish(EventType.GUIDING_STOPPED)

    def reset(self):
        self.stop_guiding()
        self._error_history.clear()
        self._ra_oscillations = 0
        self._m_inv = None
        self._ref_x = None
        self._ref_y = None

    # =========================================================================
    # LOOP PRINCIPAL
    # =========================================================================

    def _guiding_loop(self):
        while self._guiding:
            loop_start = time.time()
            try:
                correction_sent = self._guide_step()
            except Exception as e:
                logger.error(f"Error: {e}", exc_info=True)
                correction_sent = False
            
            elapsed = time.time() - loop_start
            if not correction_sent:
                sleep_time = max(0.0, (1.0 / self._guide_frequency) - elapsed)
                time.sleep(sleep_time)

    def _guide_step(self):
        """Un ciclo completo de guiado."""

        # --- 1. Capturar frame y detectar centroide ---
        centroid = self._measure_centroid()
        if centroid is None:
            logger.debug("Sin centroide en este frame, saltando ciclo")
            return

        cx, cy = centroid

        # --- 2. Establecer referencia en el primer frame ---
        if self._ref_x is None:
            self._ref_x = cx
            self._ref_y = cy
            logger.info(f"Referencia de guiado establecida: ({cx:.2f}, {cy:.2f})")
            return  # El primer frame solo establece referencia, no corrige

        # --- 3. Calcular error en píxeles ---
        dx = cx - self._ref_x
        dy = cy - self._ref_y

        # --- 4. Leer parámetros del contexto en tiempo de ejecución ---
        # El usuario puede cambiar px_scale, min_mo, agresividad y
        # exposición desde la UI durante el guiado.
        with self.ctx.read() as ctx:
            px_scale    = ctx.user.px_scale
            min_mo      = ctx.user.min_mo
            aggressive  = ctx.user.aggressiveness
            exposure_us = ctx.camera.expo_time * 1_000_000
            ctx_star_x  = ctx.star.centroid_x
            ctx_star_y  = ctx.star.centroid_y

        # --- 4. Convertir a arcosegundos ---
        # [DIAG-A] Centroide actual, referencia de guiado y estrella del contexto.
        # Señal de problema: ctx_star muy diferente de ref → posible cambio de estrella.
        logger.debug(
            f"[DIAG-A] centroide=({cx:.2f},{cy:.2f}) "
            f"ref=({self._ref_x:.2f},{self._ref_y:.2f}) "
            f"ctx_star=({ctx_star_x:.2f},{ctx_star_y:.2f}) "
            f"→ dx={dx:+.3f}px dy={dy:+.3f}px"
        )

        ra_arcsec    = dx * px_scale
        dec_arcsec   = dy * px_scale
        total_arcsec = math.sqrt(ra_arcsec**2 + dec_arcsec**2)
        total_pixels = math.sqrt(dx**2 + dy**2)

        error = GuideError(
            ra_arcsec=round(ra_arcsec, 3),
            dec_arcsec=round(dec_arcsec, 3),
            ra_pixels=round(dx, 3),
            dec_pixels=round(dy, 3),
            total_arcsec=round(total_arcsec, 3),
            total_pixels=round(total_pixels, 3)
        )

                # [NUEVO] Publicar centroide para dibujar recuadro en UI
        self.event_bus.publish(
            EventType.GUIDING_ERROR,
            ra=error.ra_arcsec,
            dec=error.dec_arcsec,
            total=error.total_arcsec,
            centroid=(cx, cy),  # ← NUEVO
            label=f"Centroide: ({cx:.1f}, {cy:.1f})"
        )

        self._error_history.append(error)

        # --- 5. Actualizar oscilaciones RA ---
        current_ra_sign = 1 if error.ra_arcsec >= 0 else -1
        if self._last_ra_sign != 0 and current_ra_sign != self._last_ra_sign:
            self._ra_oscillations += 1
        self._last_ra_sign = current_ra_sign

        # --- 6. Actualizar contexto y publicar para UI ---
        with self.ctx.write() as ctx:
            ctx.errors.ra              = error.ra_arcsec
            ctx.errors.dec             = error.dec_arcsec
            ctx.errors.total           = error.total_arcsec
            ctx.errors.ra_oscillations = self._ra_oscillations

        stats = self._calculate_stats()
        self.event_bus.publish(
            EventType.GUIDING_STATS,
            ra_rms_px=stats['ra_rms_px'],
            ra_rms_arc=stats['ra_rms_arc'],
            dec_rms_px=stats['dec_rms_px'],
            dec_rms_arc=stats['dec_rms_arc'],
            total_rms_px=stats['total_rms_px'],
            total_rms_arc=stats['total_rms_arc'],
            oscillations=self._ra_oscillations
        )

        # --- 7. Verificar MinMo ---
        if total_arcsec < min_mo:
            logger.debug(
                f"Error {total_arcsec:.3f}\" < MinMo {min_mo:.3f}\". Sin corrección."
            )
            return

        # --- 8. Calcular pulso con M_inv ---
        error_vec = np.array([dx, dy])
        pulse_vec = self._m_inv @ error_vec  # [ms_RA, ms_DEC]

        ms_ra  = float(pulse_vec[0])
        ms_dec = float(pulse_vec[1])

        # [DIAG-B] Pulso raw de M_inv antes de agresividad ni clamp.
        # Señal de problema: signo(ms_ra) != signo(dx) → corrección divergente.
        logger.debug(
            f"[DIAG-B] M_inv@[{dx:+.3f},{dy:+.3f}] "
            f"→ ms_ra={ms_ra:+.1f}ms ms_dec={ms_dec:+.1f}ms "
            f"(esperado: signo(ms_ra)==signo(dx), signo(ms_dec)==signo(dy))"
        )

        # --- 9. Aplicar agresividad ---
        factor = aggressive / 100.0
        ms_ra  *= factor
        ms_dec *= factor

        # --- 10. Calcular límite de pulso según exposición ---
        # Evita pulsos que ocupen más fracción de la exposición que max_ratio,
        # lo cual dejaría la cámara integrando durante el movimiento.
        exposure_ms = exposure_us / 1000.0
        pulse_limit = min(
            self._max_pulse_ms,
            exposure_ms * self._max_ratio
        )

        # El signo de ms indica la dirección de corrección
        # (corrección opuesta al error: dx>0 → RA-, dx<0 → RA+)
        ra_direction  = "RA-"  if ms_ra  >= 0 else "RA+"
        dec_direction = "DEC-" if ms_dec >= 0 else "DEC+"
        ra_duration   = int(min(abs(ms_ra),  pulse_limit))
        dec_duration  = int(min(abs(ms_dec), pulse_limit))

        # [DIAG-C] Pulso final post-agresividad y post-clamp.
        # Señal de problema: pulse_limit truncando sistemáticamente → subir max_pulse_ms.
        logger.debug(
            f"[DIAG-C] agresividad={aggressive:.0f}% limit={pulse_limit:.0f}ms "
            f"→ {ra_direction} {ra_duration}ms (raw={abs(ms_ra/factor):.0f}ms) "
            f"| {dec_direction} {dec_duration}ms (raw={abs(ms_dec/factor):.0f}ms)"
        )

        # --- 11. Enviar pulsos ---
        # Hardware real: guide() es BLOQUEANTE (GPIO).
        # Se envían secuencialmente: RA primero, luego DEC.
        if ra_duration > 0:
            self._telescope.guide(ra_direction, ra_duration)
            logger.debug(f"Pulso RA:  {ra_direction} {ra_duration}ms")

        if dec_duration > 0:
            self._telescope.guide(dec_direction, dec_duration)
            logger.debug(f"Pulso DEC: {dec_direction} {dec_duration}ms")

        # Después de enviar pulsos RA y DEC
        correction_sent = (ra_duration > 0) or (dec_duration > 0)
    
        if correction_sent:
            wait = (max(ra_duration, dec_duration) / 1000.0) + (exposure_us / 1_000_000)
            time.sleep(wait)
            self._last_frame_hash = None
        return correction_sent

    # =========================================================================
    # MEDICIÓN DE CENTROIDE
    # =========================================================================

    def _measure_centroid(self) -> Optional[tuple]:
            """
            Captura un frame y retorna el centroide de la estrella guía.
            Usa la posición guardada en el contexto para identificar la estrella
            correcta si hay múltiples detecciones.
            """

            import hashlib
            import numpy as np
            frame = self._camera.capture_single_frame()
            if frame is None:
                return None

            # Detectar frame stale
            frame_bytes = np.array(frame).tobytes()
            frame_hash = hash(frame_bytes)
            if frame_hash == self._last_frame_hash:
                logger.debug("[DIAG-0] Frame stale detectado — saltando ciclo")
                return None
            self._last_frame_hash = frame_hash

            detections = self._processor.process(frame)
            if not detections:
                return None

            # Usar la estrella más cercana a la referencia del contexto
            with self.ctx.read() as ctx:
                ref_x = ctx.star.centroid_x
                ref_y = ctx.star.centroid_y
                has_ref = ctx.star.num_estrella > 0
                guide_snr = ctx.star.snr  # ← SNR de la estrella guía seleccionada

            anchor_x = self._ref_x if self._ref_x is not None else ref_x
            anchor_y = self._ref_y if self._ref_y is not None else ref_y
            if len(detections) > 1:
                best = min(
                    detections,
                    key=lambda s: (s.x - anchor_x)**2 + (s.y - anchor_y)**2
                )
            else:
                best = detections[0]

            # =========================================================================
            # FILTRO ANTI-ESTRELLAS FALSAS
            # =========================================================================

            # 1. SNR dinámico: umbral = SNR de la estrella guía × factor de tolerancia
            # Si la estrella guía tiene SNR=100, el umbral será 50 (50%).
            # Esto evita que una estrella brillante acepte detecciones débiles,
            # y una estrella débil no sea demasiado exigente.
            SNR_TOLERANCE = 0.5  # La detección debe tener al menos 50% del SNR original
            #min_snr_dynamic = guide_snr * SNR_TOLERANCE if guide_snr > 0 else 20.0
            min_snr_dynamic = 10.0

            if best.snr < min_snr_dynamic:
                logger.warning(
                    f"[DIAG-FAKE] Estrella descartada por SNR bajo: "
                    f"SNR={best.snr:.1f} < umbral_dinámico={min_snr_dynamic:.1f} "
                    f"(guía_SNR={guide_snr:.1f} × {SNR_TOLERANCE})"
                )
                return None

            # 2. Distancia máxima a la referencia de guiado
            MAX_DRIFT_PX = 10.0  # px; ajustar según escala y seeing

            if self._ref_x is not None:
                dist_to_ref = math.sqrt((best.x - self._ref_x)**2 + (best.y - self._ref_y)**2)
                if dist_to_ref > MAX_DRIFT_PX:
                    logger.warning(
                        f"[DIAG-FAKE] Estrella descartada por distancia: "
                        f"dist_ref={dist_to_ref:.1f}px > {MAX_DRIFT_PX}px "
                        f"ref=({self._ref_x:.1f},{self._ref_y:.1f}) "
                        f"detección=({best.x:.1f},{best.y:.1f})"
                    )
                    return None

            # [DIAG-0] Qué estrella seleccionó _measure_centroid y cuántas detectó.
            dist_to_ctx = math.sqrt((best.x - ref_x)**2 + (best.y - ref_y)**2)
            logger.debug(
                f"[DIAG-0] n_det={len(detections)} "
                f"elegida=({best.x:.1f},{best.y:.1f}) snr={best.snr:.1f} "
                f"dist_ctx={dist_to_ctx:.1f}px dist_ref={dist_to_ref if self._ref_x else 'N/A'}px"
            )
            return (best.x, best.y)
    # =========================================================================
    # ESTADÍSTICAS RMS
    # =========================================================================

    def _calculate_stats(self) -> dict:
        if len(self._error_history) < 2:
            return {k: 0.0 for k in [
                'ra_rms_px', 'ra_rms_arc',
                'dec_rms_px', 'dec_rms_arc',
                'total_rms_px', 'total_rms_arc'
            ]}

        def rms(values):
            return math.sqrt(sum(v**2 for v in values) / len(values))

        return {
            'ra_rms_px':     round(rms([e.ra_pixels    for e in self._error_history]), 3),
            'ra_rms_arc':    round(rms([e.ra_arcsec    for e in self._error_history]), 3),
            'dec_rms_px':    round(rms([e.dec_pixels   for e in self._error_history]), 3),
            'dec_rms_arc':   round(rms([e.dec_arcsec   for e in self._error_history]), 3),
            'total_rms_px':  round(rms([e.total_pixels for e in self._error_history]), 3),
            'total_rms_arc': round(rms([e.total_arcsec for e in self._error_history]), 3),
        }