"""Constantes del módulo Scanner (antes core/config.py del proyecto viejo)."""
from src.platform.config.settings import settings

TESSERACT_CMD: str = settings.SCANNER_TESSERACT_CMD

EXTENSIONES_VALIDAS: frozenset[str] = frozenset(
    {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".xlsx"}
)
TAMANO_MAXIMO: int = 30 * 1024 * 1024  # 30 MB (planillas escaneadas multipágina)
