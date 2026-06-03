# interface/view.py
"""
Vista principal - Solo renderiza, no tiene lógica de negocio.
Versión mejorada de maqueta_0.py con capacidad de actualización por estado.
"""

import customtkinter as ctk
from PIL import Image
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from collections import deque
from typing import Callable, Optional, Dict, Set
from dataclasses import dataclass

# Configuración global de apariencia
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


@dataclass
class WidgetReferences:
    """Referencia centralizada a todos los widgets interactivos."""
    # Botones principales
    calibrar: ctk.CTkButton
    detener: ctk.CTkButton
    comenzar: ctk.CTkButton
    
    # Botones cámara
    px_scale: ctk.CTkButton
    dist_focal: ctk.CTkButton
    bucle: ctk.CTkButton
    exposicion: ctk.CTkButton
    num_estrella: ctk.CTkButton
    ganancia: ctk.CTkButton
    
    # Botones gráficos
    x_scale: ctk.CTkButton
    y_scale: ctk.CTkButton
    agresividad: ctk.CTkButton
    min_mo: ctk.CTkButton


class MainWindow(ctk.CTk):
    """
    Ventana principal del sistema de autoguiado.
    Responsabilidad única: Renderizar la UI y recibir eventos de usuario.
    No contiene lógica de qué mostrar (eso lo decide StateRenderer).
    """
    
    def __init__(self):
        super().__init__()
        self.title("AUTO GUIADO MINI - Arquitectura Limpia")
        self.geometry("1100x750")
        
        # Callbacks externos (inyectados por el adaptador)
        self.on_button_click: Optional[Callable[[str], None]] = None
        self.on_closing_request: Optional[Callable[[], None]] = None
        
        # Referencias a widgets para habilitación/deshabilitación
        self.widgets: Optional[WidgetReferences] = None
        
        # Cola thread-safe para actualizaciones
        self._update_queue = deque()
        self._max_graph_points = 500
        
        # Estado del gráfico
        self._graph_state = None
        
        self._setup_ui()
        self._start_queue_processor()
        
        # Protocolo de cierre
        self.protocol("WM_DELETE_WINDOW", self._handle_close)
    
    # =========================================================================
    # SETUP UI - Construcción de la interfaz (tu código original estructurado)
    # =========================================================================
    
    def _setup_ui(self):
        """Construye toda la interfaz."""
        self._setup_top_panel()
        self._setup_main_container()
        self._setup_status_bar()
        self._capture_widget_references()
    
    def _setup_top_panel(self):
        """Panel superior con botones principales."""
        self.frame_top = ctk.CTkFrame(self)
        self.frame_top.pack(pady=10, padx=10, fill="x")
        
        self._btn_calibrar = ctk.CTkButton(
            self.frame_top, text="Automatic Calibration", 
            fg_color="gray", command=lambda: self._notify_button("Automatic Calibration")
        )
        self._btn_calibrar.pack(side="left", padx=10)
        
        self._btn_detener = ctk.CTkButton(
            self.frame_top, text="Stop", fg_color="#661111",
            command=lambda: self._notify_button("Stop")
        )
        self._btn_detener.pack(side="left", padx=10)
        
        self._btn_comenzar = ctk.CTkButton(
            self.frame_top, text="Start", fg_color="#116611",
            command=lambda: self._notify_button("Start")
        )
        self._btn_comenzar.pack(side="left", padx=10)
    
    def _setup_main_container(self):
        """Contenedor principal dividido en 3 columnas."""
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.pack(expand=True, fill="both", padx=10)
        
        self._setup_camera_column()
        self._setup_graphs_column()
        self._setup_panel_column()
    
    def _setup_camera_column(self):
        """Columna izquierda: Controles de cámara y visualización."""
        self.left_column = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.left_column.grid(row=0, column=0, padx=10, pady=10)
        
        # Controles superiores
        cam_top = ctk.CTkFrame(self.left_column, fg_color="transparent")
        cam_top.pack(fill="x", pady=5)
        
        self._btn_px_scale = ctk.CTkButton(
            cam_top, text="Cam Gde Px Scale", width=140,
            command=lambda: self._notify_button("Cam Gde Px Scale")
        )
        self._btn_px_scale.pack(side="left", padx=5)
        
        self._btn_dist_focal = ctk.CTkButton(
            cam_top, text="Focal Dist", width=140,
            command=lambda: self._notify_button("Dist Focal")
        )
        self._btn_dist_focal.pack(side="left", padx=5)
        
        # Frame de cámara
        self.cam_frame = ctk.CTkFrame(self.left_column, width=400, height=300, border_width=2)
        self.cam_frame.pack(pady=5)
        self.cam_frame.pack_propagate(False)
        
        self.cam_display = ctk.CTkLabel(self.cam_frame, text="[ Visualización de Cámara ]")
        self.cam_display.place(relx=0.5, rely=0.5, anchor="center")
        
        # Controles inferiores fila 1
        cam_bot1 = ctk.CTkFrame(self.left_column, fg_color="transparent")
        cam_bot1.pack(fill="x", pady=2)
        
        self._btn_bucle = ctk.CTkButton(
            cam_bot1, text="Bucle", width=140, fg_color="#444444",
            command=lambda: self._notify_button("Bucle")
        )
        self._btn_bucle.pack(side="left", padx=5)
        
        self._btn_exposicion = ctk.CTkButton(
            cam_bot1, text="Expo Time", width=140, fg_color="#444444",
            command=lambda: self._notify_button("Expo Time")
        )
        self._btn_exposicion.pack(side="left", padx=5)
        
        # Controles inferiores fila 2
        cam_bot2 = ctk.CTkFrame(self.left_column, fg_color="transparent")
        cam_bot2.pack(fill="x", pady=2)
        
        self._btn_num_estrella = ctk.CTkButton(
            cam_bot2, text="Num Star", width=140, fg_color="#444444",
            command=lambda: self._notify_button("Num Star")
        )
        self._btn_num_estrella.pack(side="left", padx=5)
        
        self._btn_ganancia = ctk.CTkButton(
            cam_bot2, text="Gain", width=140, fg_color="#444444",
            command=lambda: self._notify_button("Gain")
        )
        self._btn_ganancia.pack(side="left", padx=5)
    
    def _setup_graphs_column(self):
        """Columna central: Gráficos y parámetros."""
        self.right_column = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.right_column.grid(row=0, column=1, padx=10, pady=10, sticky="n")
        
        # Controles de escala
        graph_ctrl = ctk.CTkFrame(self.right_column, fg_color="transparent")
        graph_ctrl.pack(fill="x", pady=5)
        
        self._btn_x_scale = ctk.CTkButton(
            graph_ctrl, text="X scale", width=90,
            command=lambda: self._notify_button("X scale")
        )
        self._btn_x_scale.grid(row=0, column=0, padx=2)
        
        self._btn_y_scale = ctk.CTkButton(
            graph_ctrl, text="Y scale", width=90,
            command=lambda: self._notify_button("Y scale")
        )
        self._btn_y_scale.grid(row=0, column=1, padx=2)
        
        self._btn_agresividad = ctk.CTkButton(
            graph_ctrl, text="Agresividad", width=90,
            command=lambda: self._notify_button("Agresividad")
        )
        self._btn_agresividad.grid(row=0, column=2, padx=2)
        
        self._btn_min_mo = ctk.CTkButton(
            graph_ctrl, text="MinMo", width=90,
            command=lambda: self._notify_button("MinMo")
        )
        self._btn_min_mo.grid(row=0, column=3, padx=2)
        
        # Gráfico matplotlib
        self.graph_frame = ctk.CTkFrame(self.right_column, width=400, height=200, border_width=1)
        self.graph_frame.pack(pady=10)
        self.graph_frame.pack_propagate(False)
        
        self._setup_matplotlib_graph()
        
        # Paneles de información divididos
        data_container = ctk.CTkFrame(self.right_column, fg_color="transparent")
        data_container.pack(fill="x", pady=10)
        
        # Panel izquierdo: Parámetros del sistema
        self.frame_params = ctk.CTkFrame(data_container, width=195, height=200)
        self.frame_params.pack(side="left", padx=(0, 5), expand=True, fill="both")
        
        self.lbl_params = ctk.CTkLabel(self.frame_params, text="", justify="left")
        self.lbl_params.place(relx=0.5, rely=0.5, anchor="center")
        
        # Panel derecho: Errores en tiempo real
        self.frame_errors = ctk.CTkFrame(data_container, width=195, height=200)
        self.frame_errors.pack(side="left", padx=(5, 0), expand=True, fill="both")
        
        self.lbl_errors = ctk.CTkLabel(
            self.frame_errors, text="", justify="left", text_color="#FFCC00"
        )
        self.lbl_errors.place(relx=0.5, rely=0.5, anchor="center")
    
    def _setup_matplotlib_graph(self):
        """Configura el gráfico de errores."""
        self.fig = Figure(figsize=(4, 2), dpi=100, facecolor='#2b2b2b')
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor('#2b2b2b')
        
        # Estilo oscuro
        self.ax.tick_params(axis='x', colors='white')
        self.ax.tick_params(axis='y', colors='white')
        for spine in self.ax.spines.values():
            spine.set_color('white')
        
        self.line_ra, = self.ax.plot([], [], 'r-', label='RA Error', linewidth=1)
        self.line_dec, = self.ax.plot([], [], 'c-', label='Dec Error', linewidth=1)
        
        self.ax.set_xlabel('Muestras', color='white')
        self.ax.set_ylabel('Error (arcsec)', color='white')
        self.ax.legend(
            loc='upper right', facecolor='#2b2b2b', 
            edgecolor='white', labelcolor='white'
        )
        self.ax.grid(True, alpha=0.3, color='gray')
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        
        # Datos
        self.ra_data = []
        self.dec_data = []
        self.time_data = []
        self.time_counter = 0
    
    def _setup_panel_column(self):
        """Columna derecha: Panel de configuración dinámico."""
        self.right_panel = ctk.CTkFrame(
            self.main_container, width=250, border_width=2
        )
        self.right_panel.grid(row=0, column=2, padx=10, pady=10, sticky="nsew")
        self.right_panel.grid_propagate(False)
        
        ctk.CTkLabel(
            self.right_panel, text="Panel Manager",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=10, padx=10)
        
        self.config_content = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        self.config_content.pack(expand=True, fill="both")
    
    def _setup_status_bar(self):
        """Barra de estado inferior."""
        self.status_bar = ctk.CTkLabel(
            self, text="Inicializando...", 
            fg_color="#333333", corner_radius=5
        )
        self.status_bar.pack(side="bottom", fill="x", padx=10, pady=5)
    
    def _capture_widget_references(self):
        """Guarda referencias para habilitación/deshabilitación dinámica."""
        self.widgets = WidgetReferences(
            calibrar=self._btn_calibrar,
            detener=self._btn_detener,
            comenzar=self._btn_comenzar,
            px_scale=self._btn_px_scale,
            dist_focal=self._btn_dist_focal,
            bucle=self._btn_bucle,
            exposicion=self._btn_exposicion,
            num_estrella=self._btn_num_estrella,
            ganancia=self._btn_ganancia,
            x_scale=self._btn_x_scale,
            y_scale=self._btn_y_scale,
            agresividad=self._btn_agresividad,
            min_mo=self._btn_min_mo
        )
    
    # =========================================================================
    # MÉTODOS PÚBLICOS - API para el StateRenderer y servicios externos
    # =========================================================================

    def create_list_selector(self, titulo: str, opciones: list, callback_ok):
        """
        Crea selector de lista con opciones pre-formateadas (incluye SNR).
        Versión segura: usa after() para evitar conflictos con eventos de Tkinter.
        """
        def _create():
            self.clear_panel()
            
            ctk.CTkLabel(
                self.config_content, text=titulo, 
                font=("Arial", 14, "bold")
            ).pack(pady=10)
            
            combo = ctk.CTkComboBox(self.config_content, values=opciones)
            if opciones:
                combo.set(opciones[0])
            combo.pack(pady=5)
            
            ctk.CTkButton(
                self.config_content, text="OK",
                command=lambda: callback_ok(combo.get())
            ).pack(pady=10)
        
        # Ejecutar en el siguiente ciclo del mainloop para evitar conflictos
        self.after(10, _create)

    def set_button_enabled(self, button_name: str, enabled: bool):
        """Habilita o deshabilita un botón específico."""
        if not self.widgets:
            return
            
        button_map = {
            'calibrar': self.widgets.calibrar,
            'detener': self.widgets.detener,
            'comenzar': self.widgets.comenzar,
            'px_scale': self.widgets.px_scale,
            'dist_focal': self.widgets.dist_focal,
            'bucle': self.widgets.bucle,
            'exposicion': self.widgets.exposicion,
            'num_estrella': self.widgets.num_estrella,
            'ganancia': self.widgets.ganancia,
            'x_scale': self.widgets.x_scale,
            'y_scale': self.widgets.y_scale,
            'agresividad': self.widgets.agresividad,
            'min_mo': self.widgets.min_mo,
        }
        
        btn = button_map.get(button_name)
        if btn:
            if enabled:
                btn.configure(state="normal", fg_color=self._get_default_color(button_name))
            else:
                btn.configure(state="disabled", fg_color="gray")
    
    def set_buttons_enabled(self, buttons: Set[str], enabled: bool = True):
        """Habilita/deshabilita múltiples botones."""
        for btn in buttons:
            self.set_button_enabled(btn, enabled)
    
    def set_status_message(self, message: str, color: Optional[str] = None):
        """Actualiza el mensaje de la barra de estado."""
        self.status_bar.configure(text=message)
        if color:
            self.status_bar.configure(fg_color=color)
    
    def show_warning(self, message: str):
        """Muestra un diálogo de advertencia temporal."""
        self.set_status_message(f"⚠️ {message}", color="#664411")
        # Auto-limpiar después de 3 segundos
        self.after(3000, lambda: self.set_status_message("Listo", color="#333333"))
    
    def update_camera_image(self, image_pil: Image.Image):
        """Actualiza la imagen de la cámara."""
        img_ctk = ctk.CTkImage(
            light_image=image_pil, 
            dark_image=image_pil, 
            size=(400, 300)
        )
        self.cam_display.configure(image=img_ctk, text="")
        # Guardar referencia para evitar GC
        self._current_cam_image = img_ctk
    
    def update_camera_image_with_centroid(self, image_pil: Image.Image, 
                                        centroid: Optional[tuple] = None,
                                        label: Optional[str] = None):
        """
        Muestra imagen de cámara con un recuadro rojo alrededor del centroide detectado.
        
        Args:
            image_pil: Imagen PIL original
            centroid: (x, y) del centroide detectado, o None para imagen sin recuadro
            label: Texto opcional para mostrar (ej: "Centroide: (123.4, 456.7)")
        """
        from PIL import ImageDraw, ImageFont
        
        if centroid is not None:
            # Crear copia para no modificar la original
            img_copy = image_pil.copy()
            draw = ImageDraw.Draw(img_copy)
            
            cx, cy = centroid
            box_size = 20  # Tamaño del recuadro en píxeles (10px a cada lado)
            
            # Coordenadas del recuadro
            x1 = max(0, int(cx - box_size))
            y1 = max(0, int(cy - box_size))
            x2 = min(image_pil.width - 1, int(cx + box_size))
            y2 = min(image_pil.height - 1, int(cy + box_size))
            
            # Dibujar recuadro rojo (2px de grosor)
            draw.rectangle([x1, y1, x2, y2], outline="red", width=2)
            
            # Texto con coordenadas
            if label:
                text = label
            else:
                text = f"({cx:.1f}, {cy:.1f})"
            
            # Dibujar fondo negro semi-transparente para el texto
            text_bbox = draw.textbbox((0, 0), text)
            text_w = text_bbox[2] - text_bbox[0]
            text_h = text_bbox[3] - text_bbox[1]
            draw.rectangle(
                [x1, y1 - text_h - 4, x1 + text_w + 4, y1],
                fill="black"
            )
            draw.text((x1 + 2, y1 - text_h - 2), text, fill="yellow")
            
            self.update_camera_image(img_copy)
        else:
            self.update_camera_image(image_pil)

    def update_system_params(self, px_scale: float, angle: float,
                            vel_ra: float, vel_dec: float,
                            steps: int, ort_error: float):
        """Actualiza el panel izquierdo con resultados de calibración."""
        texto = (
            f"Escala: {px_scale:.3f} \"/px\n"
            f"Ángulo: {angle:.2f}°\n"
            f"Vel RA: {vel_ra:.4f} px/s\n"
            f"Vel DEC: {vel_dec:.4f} px/s\n"
            f"Frames: {steps}\n"
            f"Ort err: {ort_error:.2f}°"
        )
        self.lbl_params.configure(text=texto)

    def draw_centroid_overlay(self, centroid: tuple):
        """
        Dibuja un recuadro rojo sobre la imagen actual SIN reenviar imagen nueva.
        Usa CTkLabel con una imagen PIL modificada.
        """
        from PIL import ImageDraw
        
        # Recuperar la imagen actual que está mostrando el label
        if not hasattr(self, '_current_cam_image'):
            print("[DEBUG] No hay imagen actual en el label")
            return
        
        # CTkImage no expone la imagen PIL interna, así que necesitamos
        # guardar la última imagen PIL por separado
        if not hasattr(self, '_current_pil_image'):
            print("[DEBUG] No hay imagen PIL guardada")
            return
        
        img_copy = self._current_pil_image.copy()
        draw = ImageDraw.Draw(img_copy)
        cx, cy = centroid
        
        # Recuadro rojo de 30x30px
        box_size = 15
        x1 = max(0, int(cx - box_size))
        y1 = max(0, int(cy - box_size))
        x2 = min(img_copy.width - 1, int(cx + box_size))
        y2 = min(img_copy.height - 1, int(cy + box_size))
        
        draw.rectangle([x1, y1, x2, y2], outline="red", width=3)
        # Cruz en el centro
        draw.line([(cx-5, cy), (cx+5, cy)], fill="red", width=2)
        draw.line([(cx, cy-5), (cx, cy+5)], fill="red", width=2)
        
        # Actualizar el label con la imagen modificada
        self.update_camera_image(img_copy)
        print(f"[DEBUG] Recuadro dibujado en ({cx:.1f}, {cy:.1f})")

    def update_camera_image(self, image_pil: Image.Image):
        """Actualiza la imagen de la cámara."""
        # [NUEVO] Guardar referencia a la imagen PIL para poder dibujar sobre ella
        self._current_pil_image = image_pil
        
        img_ctk = ctk.CTkImage(
            light_image=image_pil, 
            dark_image=image_pil, 
            size=(400, 300)
        )
        self.cam_display.configure(image=img_ctk, text="")
        # Guardar referencia para evitar GC
        self._current_cam_image = img_ctk
    
    def update_error_display(self, ra_px: float, ra_arc: float,
                            dec_px: float, dec_arc: float,
                            tot_px: float, tot_arc: float, osc: int):
        """Actualiza el panel de errores en tiempo real."""
        texto = (
            f"RMS Error (px)\n"
            f"RA: {ra_px:.2f} ({ra_arc:.2f}\")\n"
            f"Dec: {dec_px:.2f} ({dec_arc:.2f}\")\n"
            f"Tot: {tot_px:.2f} ({tot_arc:.2f}\")\n"
            f"RA Oscs: {osc}"
        )
        self.lbl_errors.configure(text=texto)
    
    def clear_panel(self):
        """Limpia el panel de configuración de forma segura."""
        # Obtener lista de hijos antes de destruir (evita modificación durante iteración)
        children = list(self.config_content.winfo_children())
        for widget in children:
            try:
                widget.destroy()
            except Exception:
                pass  # Ignorar errores si el widget ya fue destruido
    
    def show_text_in_panel(self, text: str):
        """Muestra texto simple en el panel."""
        def _create():
            self.clear_panel()
            label = ctk.CTkLabel(self.config_content, text=text, wraplength=200)
            label.pack(pady=20)
        
        self.after(10, _create)
    
    def create_text_input(self, title: str, default_value: float, 
                         on_confirm: Callable[[str], None]):
        """Crea input de texto en el panel."""
        def _create():
            self.clear_panel()
            
            ctk.CTkLabel(
                self.config_content, text=title, 
                font=("Arial", 14, "bold")
            ).pack(pady=10)
            
            entrada = ctk.CTkEntry(
                self.config_content, 
                placeholder_text=str(default_value)
            )
            entrada.pack(pady=5)
            
            ctk.CTkButton(
                self.config_content, text="OK",
                command=lambda: on_confirm(entrada.get())
            ).pack(pady=10)
        
        self.after(10, _create)
    
    def create_step_adjustment(self, title: str, initial: float,
                               on_confirm: Callable[[float], None]):
        """Crea control +/- en el panel."""
        def _create():
            self.clear_panel()
            
            ctk.CTkLabel(
                self.config_content, text=title,
                font=("Arial", 14, "bold")
            ).pack(pady=10)
            
            frame = ctk.CTkFrame(self.config_content, fg_color="transparent")
            frame.pack(pady=5)
            
            valor = ctk.DoubleVar(value=initial)
            lbl = ctk.CTkLabel(frame, textvariable=valor, font=("Arial", 16))
            
            ctk.CTkButton(
                frame, text="-", width=30,
                command=lambda: valor.set(round(valor.get() - 1, 2))
            ).pack(side="left", padx=5)
            lbl.pack(side="left", padx=10)
            ctk.CTkButton(
                frame, text="+", width=30,
                command=lambda: valor.set(round(valor.get() + 1, 2))
            ).pack(side="left", padx=5)
            
            ctk.CTkButton(
                self.config_content, text="OK",
                command=lambda: on_confirm(valor.get())
            ).pack(pady=20)
        
        self.after(10, _create)
    
    def create_slider(self, title: str, min_val: float, max_val: float,
                     step: float, initial: float,
                     on_confirm: Callable[[float], None]):
        """Crea slider en el panel."""
        def _create():
            self.clear_panel()
            
            ctk.CTkLabel(
                self.config_content, text=title,
                font=("Arial", 14, "bold")
            ).pack(pady=10)
            
            valor = ctk.DoubleVar(value=initial)
            ctk.CTkLabel(
                self.config_content, textvariable=valor,
                font=("Arial", 16)
            ).pack()
            
            pasos = int((max_val - min_val) / step)
            slider = ctk.CTkSlider(
                self.config_content, from_=min_val, to=max_val,
                number_of_steps=pasos, variable=valor
            )
            slider.pack(pady=10, padx=20)
            
            ctk.CTkButton(
                self.config_content, text="OK",
                command=lambda: on_confirm(valor.get())
            ).pack(pady=10)
        
        self.after(10, _create)
    
    # =========================================================================
    # GRÁFICOS - Manejo de datos en tiempo real
    # =========================================================================
    def update_camera_image_with_detections(self, image_pil: Image.Image, 
                                           star_selection_service=None):
        """
        Actualiza imagen de cámara con recuadros de estrellas detectadas.
        """
        if star_selection_service and hasattr(star_selection_service, 'get_annotated_image'):
            # Dibujar detecciones sobre la imagen
            annotated = star_selection_service.get_annotated_image(image_pil)
            self.update_camera_image(annotated)
        else:
            self.update_camera_image(image_pil)
    
    
    def bind_graph_state(self, graph_params):
        """Vincula parámetros de escala del gráfico."""
        self._graph_state = graph_params
        self._apply_graph_scale()
    
    def add_error_point(self, ra_error: float, dec_error: float, tiempo: Optional[int] = None):
        """Thread-safe: Añade punto al gráfico."""
        if tiempo is None:
            tiempo = self.time_counter
            self.time_counter += 1
        
        self._update_queue.append((tiempo, ra_error, dec_error))
    
    def _start_queue_processor(self):
        """Inicia el procesador de cola en el hilo principal."""
        self._process_queue()
    
    def _process_queue(self):
        """Procesa actualizaciones pendientes."""
        try:
            if self._graph_state:
                self._apply_graph_scale()
            
            while self._update_queue:
                tiempo, ra_err, dec_err = self._update_queue.popleft()
                
                self.time_data.append(tiempo)
                self.ra_data.append(ra_err)
                self.dec_data.append(dec_err)
                
                # Buffer circular
                if len(self.time_data) > self._max_graph_points:
                    self.time_data.pop(0)
                    self.ra_data.pop(0)
                    self.dec_data.pop(0)
                
                self._update_graph_limits()
                
                self.line_ra.set_data(self.time_data, self.ra_data)
                self.line_dec.set_data(self.time_data, self.dec_data)
                
            self.canvas.draw_idle()
            
        except Exception as e:
            print(f"Error actualizando gráfico: {e}")
        
        self.after(50, self._process_queue)
    
    def _apply_graph_scale(self):
        """Aplica límites manuales desde el estado."""
        if not self._graph_state:
            return
        
        # Verificar que el objeto tenga los atributos necesarios
        if not hasattr(self._graph_state, 'Xscale') or not hasattr(self._graph_state, 'Yscale'):
            return
            
        # Xscale: ventana temporal
        if self._graph_state.Xscale > 0:
            self._manual_x_window = int(self._graph_state.Xscale)
        else:
            self._manual_x_window = None
        
        # Yscale: rango simétrico
        if self._graph_state.Yscale > 0:
            self._manual_y_limit = (
                -float(self._graph_state.Yscale), 
                float(self._graph_state.Yscale)
            )
        else:
            self._manual_y_limit = None
    
    def _update_graph_limits(self):
        """Actualiza límites X e Y del gráfico."""
        # Eje X
        if self._manual_x_window and self.time_data:
            x_max = max(self.time_data)
            x_min = max(0, x_max - self._manual_x_window)
            self.ax.set_xlim(x_min, x_max)
        elif self.time_data:
            self.ax.set_xlim(min(self.time_data), max(self.time_data))
        
        # Eje Y
        if self._manual_y_limit:
            self.ax.set_ylim(self._manual_y_limit[0], self._manual_y_limit[1])
        else:
            all_data = self.ra_data + self.dec_data
            if all_data:
                ymin, ymax = min(all_data), max(all_data)
                margin = 0.1 if ymax == ymin else (ymax - ymin) * 0.1
                self.ax.set_ylim(ymin - margin, ymax + margin)
    
    def clear_graph(self):
        """Limpia el gráfico."""
        self.ra_data.clear()
        self.dec_data.clear()
        self.time_data.clear()
        self.time_counter = 0
        self.line_ra.set_data([], [])
        self.line_dec.set_data([], [])
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(-1, 1)
        self.canvas.draw_idle()
    
    # =========================================================================
    # MÉTODOS INTERNOS
    # =========================================================================
    
    def _notify_button(self, button_name: str):
        """Notifica al adaptador que se presionó un botón."""
        if self.on_button_click:
            self.on_button_click(button_name)
    
    def _handle_close(self):
        """Maneja el cierre de ventana."""
        if self.on_closing_request:
            self.on_closing_request()
        else:
            self._safe_close()
    
    def _safe_close(self):
        """Limpieza segura de recursos."""
        if hasattr(self, 'fig'):
            self.fig.clf()
            import matplotlib.pyplot as plt
            plt.close(self.fig)
        self.destroy()
        import sys
        sys.exit(0)
    
    def _get_default_color(self, button_name: str) -> str:
        """Retorna el color por defecto de un botón."""
        colors = {
            'detener': "#661111",
            'comenzar': "#116611",
            'bucle': "#444444",
            'exposicion': "#444444",
            'num_estrella': "#444444",
            'ganancia': "#444444",
        }
        return colors.get(button_name, ["#3B8ED0", "#1F6AA5"])  # Default CTkButton colors
    
    # =========================================================================
    # API para testing/debug
    # =========================================================================
    
    def get_current_state(self) -> Dict:
        """Retorna estado actual de la UI para testing."""
        return {
            'status_text': self.status_bar.cget("text"),
            'enabled_buttons': self._get_enabled_buttons(),
            'graph_points': len(self.time_data)
        }
    
    def _get_enabled_buttons(self) -> Set[str]:
        """Retorna set de botones habilitados."""
        enabled = set()
        if not self.widgets:
            return enabled
            
        button_map = {
            'calibrar': self.widgets.calibrar,
            'detener': self.widgets.detener,
            'comenzar': self.widgets.comenzar,
            'px_scale': self.widgets.px_scale,
            'dist_focal': self.widgets.dist_focal,
            'bucle': self.widgets.bucle,
            'exposicion': self.widgets.exposicion,
            'num_estrella': self.widgets.num_estrella,
            'ganancia': self.widgets.ganancia,
            'x_scale': self.widgets.x_scale,
            'y_scale': self.widgets.y_scale,
            'agresividad': self.widgets.agresividad,
            'min_mo': self.widgets.min_mo,
        }
        
        for name, btn in button_map.items():
            if btn.cget("state") == "normal":
                enabled.add(name)
        return enabled