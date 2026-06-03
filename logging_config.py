# logging_config.py
"""
Configuración avanzada de logging para debugging.
Importar al inicio de main.py
"""

import logging
import sys
from datetime import datetime


def setup_detailed_logging(log_file: str = "autoguiado.log"):
    """
    Configura logging detallado a archivo y consola.
    """
    
    # Formato detallado con timestamp, nivel, archivo, línea
    detailed_format = (
        "%(asctime)s | %(levelname)-8s | %(name)-25s | "
        "%(filename)s:%(lineno)d | %(funcName)s() | %(message)s"
    )
    
    # Crear handlers
    # 1. Archivo con todo (DEBUG)
    file_handler = logging.FileHandler(log_file, mode='w')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(detailed_format))
    
    # 2. Consola con INFO y above
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s"
    ))
    
    # Configurar root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Log inicial
    logging.info(f"Logging iniciado. Archivo: {log_file}")
    
    return logging.getLogger("LoggingSetup")


# Loggers específicos por módulo para filtrado fácil
def get_module_logger(module_name: str):
    """Obtiene logger configurado para un módulo."""
    return logging.getLogger(module_name)