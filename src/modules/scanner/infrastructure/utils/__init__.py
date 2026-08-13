from src.modules.scanner.infrastructure.utils.text import (
    TESSERACT_CMD,
    EXTENSIONES_IMAGEN,
    MESES,
    limpiar_texto,
    extraer_ruc,
    extraer_todos_los_ruc,
    extraer_monto,
    normalizar_fecha,
    normalizar_hora,
    extraer_serie_numero,
    extraer_mes,
    extraer_anio,
    parsear_fecha_con_mes_abreviado,
    extraer_fecha_vencimiento,
)
from src.modules.scanner.infrastructure.utils.ocr import imagen_a_texto
from src.modules.scanner.infrastructure.utils.pdf import pdf_a_texto, leer_archivo
