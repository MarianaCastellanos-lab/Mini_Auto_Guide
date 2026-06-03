# Mini Auto Guide 🌟

Sistema de autoguiado de código abierto para astrofotografía con monturas ecuatoriales.  
Desarrollado como trabajo de grado en la Pontificia Universidad Javeriana (2026).

&gt; **Objetivo pedagógico:** Caja transparente donde cada etapa —adquisición, detección, control, actuación— es inspeccionable y modificable.

---

## ¿Qué hace?

Mantiene una estrella de referencia centrada en el sensor de una cámara guía durante exposiciones largas, compensando:
- Error periódico del tornillo sinfín
- Desalineación polar
- Backlash mecánico
- Flexión térmica

**Arquitectura:** Raspberry Pi Zero 2W (servidor: cámara + ST4) ↔ PC (cliente: procesamiento + UI) vía USB-RNDIS.

---

## Hardware necesario

| Componente | Especificación | Nota |
|------------|---------------|------|
| Raspberry Pi Zero 2W | 4 núcleos ARM Cortex-A53 | Consumo &lt;2.5W, alimentación por USB |
| Cámara | Sensor Sony IMX477 (CSI) | Alta eficiencia cuántica (~70%) |
| PCB HAT | Optoacoplador PS2502-4 | Aislamiento galvánico 5000Vrms |
| Conector | RJ12 (6P6C) | Estándar ST-4 de monturas |
| Montura | Con puerto ST-4 | Ej: iOptron ZEQ25 |


---

# Instalación rápida

## 1. Cliente (PC con Windows/Linux)

git clone https://github.com/MarianaCastellanos-lab/Mini_Auto_Guide.git
cd Mini_Auto_Guide

### Crear entorno virtual (recomendado)
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt

## 2. Servidor (Raspberry Pi Zero 2W modo RNDIS)
 En la Raspberry Pi
sudo nano /usr/local/bin/setup-usb-gadget.sh
 (pegar script RNDIS del Anexo A del libro)

sudo chmod +x /usr/local/bin/setup-usb-gadget.sh
sudo systemctl enable usb-gadget.service

 Verificar IP
ip addr show usb0  # Debe mostrar 192.168.7.2

## 3. Conectar PC a la Pi
Windows: Instalar driver RNDIS → Asignar IP estática 192.168.7.1
Linux:
sudo nmcli connection add type ethernet ifname usb0 \
con-name pi-gadget ip4 192.168.7.1/24
sudo nmcli connection up pi-gadget

verifica con ping 192.168.7.2

## 4. Uso
4.1 Correr servidor en la rasberry
4.2 Correr main.py en PC

## 5. ¿Dónde modificar la lógica de guiado?
| Quiero cambiar...                   | Archivo                                 | Función clave                      |
| ----------------------------------- | --------------------------------------- | ---------------------------------- |
| Algoritmo de detección de estrellas | `application/image_processor.py`        | `detect_centroids()`               |
| Cálculo del centroide               | `application/image_processor.py`        | Centroide ponderado por intensidad |
| Ley de control (PID, etc.)          | `application/guiding_service.py`        | `calculate_correction()`           |
| Duración de pulsos ST-4             | `application/guiding_service.py`        | `M_inv @ error_pixels`             |
| Selección de estrella guía          | `application/star_selection_service.py` | `select_guide_star()`              |
| Calibración automática              | `application/calibration_service.py`    | `run_calibration()`                |
| Máquina de estados                  | `application/state_machine.py`          | Transiciones entre 6 estados       |

Documentacion detallada en docs


