import os
import tempfile

import fitz
import pdfplumber

from src.modules.scanner.infrastructure.utils.text import EXTENSIONES_IMAGEN, limpiar_texto
from src.modules.scanner.infrastructure.utils.ocr import imagen_a_texto


def pdf_a_texto(file_path: str) -> str:
    """
    Lee texto de un PDF.
    - Páginas con texto embebido: pdfplumber (preserva layout).
    - Páginas de imagen (escaneadas): fitz + OCR.
    fitz se abre una sola vez para todo el documento.
    """
    texto_total = ""

    with fitz.open(file_path) as doc_fitz:
        paginas_con_texto = [bool(p.get_text().strip()) for p in doc_fitz]

        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                if paginas_con_texto[i]:
                    t = page.extract_text()
                    if t:
                        texto_total += t.strip() + "\n"
                else:
                    pix = doc_fitz[i].get_pixmap(matrix=fitz.Matrix(4, 4))
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        img_path = tmp.name
                    try:
                        pix.save(img_path)
                        texto_total += imagen_a_texto(img_path) + "\n"
                    finally:
                        if os.path.exists(img_path):
                            os.remove(img_path)

    return texto_total


def leer_archivo(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    texto = imagen_a_texto(file_path) if ext in EXTENSIONES_IMAGEN else pdf_a_texto(file_path)
    return limpiar_texto(texto)
