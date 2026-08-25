import cv2
import numpy as np
import pytesseract
from PIL import Image

from src.modules.scanner.infrastructure.utils.text import TESSERACT_CMD


def imagen_a_texto(file_path: str) -> str:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    img = cv2.imread(file_path)
    if img is None:
        raise ValueError(f"No se pudo leer la imagen: {file_path}")
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    h, w = gris.shape
    if w < 1800:
        scale = 1800 / w
        gris = cv2.resize(gris, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gris = _corregir_rotacion(gris)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    mejorado = clahe.apply(gris)
    filtrado = cv2.bilateralFilter(mejorado, 9, 75, 75)

    binaria = cv2.adaptiveThreshold(
        filtrado, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        21, 10
    )

    texto = pytesseract.image_to_string(
        Image.fromarray(binaria), lang="spa",
        config="--psm 6 --oem 3"
    )

    # Si Tesseract lee muy poco, el documento es ilegible y cae en el fallback de
    # IA (Groq) aguas arriba, que lee mejor que un segundo motor OCR local.
    return texto


def _corregir_rotacion(gris) -> object:
    """
    Prueba 0°/90°/180°/270° con una miniatura para encontrar la orientación correcta.
    Si el score en 0° ya es bueno, evita probar las otras rotaciones.
    """
    import re

    def _score(texto: str) -> int:
        letras = len(re.findall(r'[a-zA-ZáéíóúÁÉÍÓÚñÑ]', texto))
        raros  = len(re.findall(r'[^\w\s\.,\-/:()]', texto))
        return letras - raros * 2

    img_original = Image.fromarray(gris)
    escala = min(1.0, 1500 / img_original.width)
    mini = img_original.resize(
        (int(img_original.width * escala), int(img_original.height * escala))
    )

    txt_0 = pytesseract.image_to_string(mini, lang="spa", config="--psm 6 --oem 3")
    score_0 = _score(txt_0)
    if score_0 >= 80:
        return gris

    mejor_score = score_0
    mejor_angulo = 0

    for angulo in [90, 180, 270]:
        rotada = mini.rotate(angulo, expand=True)
        txt = pytesseract.image_to_string(rotada, lang="spa", config="--psm 6 --oem 3")
        sc = _score(txt)
        if sc > mejor_score:
            mejor_score = sc
            mejor_angulo = angulo

    if mejor_angulo != 0:
        img_corregida = img_original.rotate(mejor_angulo, expand=True)
        return np.array(img_corregida)

    return gris
