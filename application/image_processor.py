# application/image_processor.py
"""
Procesador de imágenes astrofotográficas.
Detecta centroides y calcula SNR de estrellas.

ALGORITMO: Umbral global mediana + K×sigma (estándar astronómico).
  Reemplaza threshold_local (umbral adaptativo local) que no es adecuado
  para fuentes puntuales sobre fondo uniforme — imágenes estelares típicas.

  El enfoque mediana+sigma es robusto porque:
    - La mediana global no se afecta por las estrellas (< 1% de píxeles)
    - MAD × 1.4826 estima sigma del fondo sin que los picos lo inflen
    - El umbral = median + K×sigma separa limpiamente señal de ruido
    - K es el único parámetro a ajustar (3.0 funciona bien)

  Validado en simulador PHD2 con frames 752×580px, mean≈12, peak=255.
  Aplica directamente al cliente real: la Pi envía JPEG monocromático
  al 92%, que una vez decodificado tiene las mismas características.
"""

import cv2
import numpy as np
from PIL import Image
from dataclasses import dataclass
from typing import List, Tuple, Optional
from skimage.filters import gaussian
from skimage.morphology import remove_small_objects, opening, disk
from scipy import ndimage as ndi
import logging

logger = logging.getLogger("Image_process")


@dataclass
class StarDetection:
    """Estrella detectada con métricas."""
    id: int
    x: float
    y: float
    snr: float
    flux: float
    peak: float


class ImageProcessor:
    """
    Procesa imágenes de cámara para detección de estrellas.
    Optimizado para IMX477 + JPEG 92% recibido por TCP desde la Pi.
    """

    def __init__(self,
                 sigma_k: float = 3.0,
                 min_pixels: int = 3,
                 gaussian_sigma: float = 1.0,
                 snr_aperture: int = 5,
                 min_snr: float = 3.0):
        """
        Args:
            sigma_k:        Umbral = mediana + sigma_k × sigma_fondo.
                            3.0 funciona bien para hardware real.
                            Subir a 4-5 si hay demasiados falsos positivos.
                            Bajar a 2.5 si no detecta estrellas débiles.
            min_pixels:     Área mínima en píxeles para ser objeto válido.
                            3 es adecuado para estrellas puntuales de 2-4px.
            gaussian_sigma: Suavizado previo al umbral. Reduce ruido
                            introducido por la compresión JPEG.
            snr_aperture:   Radio de apertura para calcular SNR (píxeles).
            min_snr:        SNR mínimo para incluir detección en el resultado.
                            Filtra falsos positivos de ruido.
        """
        self.logger = logging.getLogger("Image_process")
        self.sigma_k     = sigma_k
        self.min_pixels  = min_pixels
        self.gauss_sigma = gaussian_sigma
        self.snr_radius  = snr_aperture
        self.min_snr     = min_snr

    def process(self, pil_image: Image.Image) -> List[StarDetection]:
        """
        Procesa imagen PIL y retorna estrellas detectadas ordenadas por SNR.

        Args:
            pil_image: Imagen PIL recibida por TCP desde la Pi (RGB o L).
                       La Pi envía JPEG monocromático — puede llegar como
                       'L' directamente o como 'RGB' con los 3 canales iguales.

        Returns:
            Lista de StarDetection ordenada por SNR descendente.
        """
        # Convertir a grayscale numpy array
        if pil_image.mode != 'L':
            img_gray = np.array(pil_image.convert('L'))
        else:
            img_gray = np.array(pil_image)

        img_float = img_gray.astype(np.float32)

        self.logger.debug(
            f"process(): mode={pil_image.mode}, size={pil_image.size}, "
            f"dtype={img_gray.dtype}, min={img_gray.min()}, "
            f"max={img_gray.max()}, mean={img_gray.mean():.1f}"
        )

        # Normalización de seguridad por si el pipeline entrega rango inesperado
        pixel_max = img_float.max()
        if pixel_max <= 1.0:
            self.logger.warning("Imagen en rango 0-1. Escalando a 0-255.")
            img_float = img_float * 255.0
        elif pixel_max > 255.0:
            self.logger.warning(f"Imagen con rango > 255 ({pixel_max:.0f}). Normalizando.")
            img_float = img_float / pixel_max * 255.0

        # 1. Suavizado gaussiano — reduce artefactos JPEG sin mover centroide
        img_smooth = gaussian(img_float, sigma=self.gauss_sigma, preserve_range=True)

        # 2. Estimación robusta del fondo
        #    La mediana global no se ve afectada por las estrellas
        #    (ocupan < 1% de los píxeles en imágenes estelares típicas)
        bg_median = float(np.median(img_smooth))
        mad       = float(np.median(np.abs(img_smooth - bg_median)))
        bg_sigma  = 1.4826 * mad  # estimación robusta de sigma gaussiana

        threshold = bg_median + self.sigma_k * bg_sigma

        self.logger.debug(
            f"Fondo: median={bg_median:.2f}, MAD={mad:.2f}, "
            f"sigma={bg_sigma:.2f}, threshold={threshold:.2f}"
        )

        # 3. Máscara binaria
        mask = img_smooth > threshold

        self.logger.debug(f"Píxeles en máscara antes de morfología: {mask.sum()}")

        # 4. Morfología mínima
        #    disk(1) para no destruir estrellas puntuales de 2-4px
        mask = opening(mask, disk(1))
        mask = remove_small_objects(mask, min_size=self.min_pixels)

        self.logger.debug(f"Píxeles en máscara después de morfología: {mask.sum()}")

        if not np.any(mask):
            self.logger.debug("Máscara vacía — sin detecciones")
            return []

        # 5. Etiquetado y centroides
        labels, n_stars = ndi.label(mask)
        self.logger.debug(f"Objetos etiquetados: {n_stars}")

        # center_of_mass pondera por intensidad → centroide subpíxel más preciso
        centroids = ndi.center_of_mass(img_smooth, labels, range(1, n_stars + 1))
        if n_stars == 1:
            centroids = [centroids]

        # 6. Calcular SNR y filtrar falsos positivos
        detections = []
        for i, centroid in enumerate(centroids, 1):
            cy, cx = centroid
            x, y   = float(cx), float(cy)
            snr, flux, peak = self._calculate_snr(img_float, x, y,
                                                   bg_median, bg_sigma)
            if snr >= self.min_snr:
                detections.append(
                    StarDetection(id=i, x=x, y=y, snr=snr, flux=flux, peak=peak)
                )

        # Ordenar por SNR descendente y re-asignar IDs según ranking
        detections.sort(key=lambda s: s.snr, reverse=True)
        for i, star in enumerate(detections, 1):
            star.id = i

        self.logger.debug(
            f"Detecciones (SNR>={self.min_snr}): n={len(detections)}, "
            + (f"top1=({detections[0].x:.1f},{detections[0].y:.1f}) "
               f"snr={detections[0].snr:.1f} peak={detections[0].peak:.0f}"
               if detections else "ninguna")
        )

        return detections

    def _calculate_snr(self, img: np.ndarray, x: float, y: float,
                       bg_median: float, bg_sigma: float
                       ) -> Tuple[float, float, float]:
        """
        Calcula SNR, flujo y pico usando fotometría de apertura.

        Reutiliza bg_median y bg_sigma ya calculados en process()
        para consistencia (no recalcula el fondo por estrella).

        Args:
            img:       Imagen float32 original (sin suavizar)
            x, y:      Centroide de la estrella
            bg_median: Mediana del fondo calculada en process()
            bg_sigma:  Sigma del fondo calculada en process()

        Returns:
            (snr, flux_total, peak_intensity)
        """
        h, w = img.shape
        y_grid, x_grid = np.ogrid[:h, :w]
        dist_sq = (x_grid - x)**2 + (y_grid - y)**2

        r_in  = self.snr_radius
        r_out = self.snr_radius * 2

        aperture_mask = dist_sq <= r_in**2
        bg_mask       = (dist_sq > r_in**2) & (dist_sq <= r_out**2)

        if not np.any(aperture_mask) or not np.any(bg_mask):
            return 0.0, 0.0, 0.0

        signal_pixels = img[aperture_mask]
        peak = float(np.max(signal_pixels))
        flux = float(np.sum(signal_pixels))

        if bg_sigma < 0.1:
            bg_sigma = 0.1

        n_aperture = float(np.sum(aperture_mask))
        net_signal = flux - (bg_median * n_aperture)
        noise      = bg_sigma * float(np.sqrt(n_aperture))
        snr        = net_signal / noise if noise > 0 else 0.0

        return snr, flux, peak

    def draw_detections(self, pil_image: Image.Image,
                        detections: List[StarDetection],
                        max_display: int = 5) -> Image.Image:
        """
        Dibuja recuadros numerados sobre las estrellas detectadas.

        Args:
            pil_image:   Imagen original PIL
            detections:  Lista de StarDetection
            max_display: Máximo de estrellas a dibujar

        Returns:
            Imagen PIL con anotaciones (RGB)
        """
        if pil_image.mode != 'RGB':
            img_rgb = pil_image.convert('RGB')
        else:
            img_rgb = pil_image.copy()

        img_np  = np.array(img_rgb)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        h, w    = img_bgr.shape[:2]

        for star in detections[:max_display]:
            x, y = int(star.x), int(star.y)

            # Color según SNR: verde=bueno, amarillo=medio, rojo=bajo
            if star.snr > 30:
                color = (0, 255, 0)
            elif star.snr > 15:
                color = (0, 255, 255)
            else:
                color = (0, 0, 255)

            half = 5
            x1, y1 = max(0, x - half), max(0, y - half)
            x2, y2 = min(w - 1, x + half), min(h - 1, y + half)

            cv2.rectangle(img_bgr, (x1, y1), (x2, y2), color, 1)
            cv2.putText(img_bgr, str(star.id), (x1, y1 - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(img_rgb)