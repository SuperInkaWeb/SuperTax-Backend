"""
Preprocesamiento de imagen antes del OCR.

Sube la tasa de acierto de Tesseract sobre fotos y escaneos de mala calidad, que
es la entrada habitual: alguien fotografía la constancia con el móvil, torcida y
con sombra. El orden de los pasos no es intercambiable y cada uno prepara al
siguiente (grises → CLAHE → denoise → deskew → binarización → escalado);
binarizar antes de igualar la iluminación pierde información irrecuperable.

Depende de OpenCV (headless) y numpy, importados de forma perezosa para no cargar
estas librerías pesadas salvo cuando se usa OCR.
"""
from __future__ import annotations

from typing import Any, Final

# Por debajo de esta altura el texto es demasiado pequeño para Tesseract.
_ALTURA_OBJETIVO: Final = 1800
# Ampliar más no mejora el reconocimiento y dispara tiempo y memoria.
_ALTURA_MAXIMA: Final = 3500
_ANGULO_MAXIMO_CORRECCION: Final = 15.0
# Por debajo de medio grado la corrección no aporta y añade un remuestreo que degrada.
_ANGULO_MINIMO_CORRECCION: Final = 0.5


def preparar_para_ocr(contenido: bytes) -> Any:  # noqa: ANN401 - ndarray de OpenCV
    """
    Devuelve la imagen lista para Tesseract. Si algún paso falla se sigue con lo que
    haya: una imagen peor preparada todavía puede leerse, y abortar garantizaría no
    leer nada.
    """
    import cv2
    import numpy as np

    bufer = np.frombuffer(contenido, dtype=np.uint8)
    imagen = cv2.imdecode(bufer, cv2.IMREAD_COLOR)
    if imagen is None:
        raise ValueError("No fue posible decodificar la imagen")

    gris = cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)
    gris = _igualar_iluminacion(gris)
    gris = _quitar_ruido(gris)
    gris = _enderezar(gris)
    gris = _escalar(gris)
    return _binarizar(gris)


def _igualar_iluminacion(gris: Any) -> Any:  # noqa: ANN401
    """CLAHE: ecualización adaptativa por bloques. La global arruina una foto con
    sombra en la mitad; CLAHE recupera la zona oscura sin quemar el resto."""
    import cv2

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    resultado: Any = clahe.apply(gris)
    return resultado


def _quitar_ruido(gris: Any) -> Any:  # noqa: ANN401
    """Filtro bilateral: suaviza el grano conservando los bordes de las letras, que
    es justo lo que el OCR necesita nítido (un gaussiano los emborronaría)."""
    import cv2

    resultado: Any = cv2.bilateralFilter(gris, d=5, sigmaColor=50, sigmaSpace=50)
    return resultado


def _enderezar(gris: Any) -> Any:  # noqa: ANN401
    """Corrige la inclinación estimándola con el rectángulo mínimo que envuelve al texto."""
    import cv2

    try:
        # Se invierte porque `minAreaRect` trabaja sobre píxeles no nulos y el texto
        # es oscuro sobre fondo claro.
        invertida = cv2.bitwise_not(gris)
        _, umbral = cv2.threshold(invertida, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        coordenadas = cv2.findNonZero(umbral)
        if coordenadas is None:
            # `findNonZero` devuelve None con una imagen completamente negra (escaneo
            # fallido); sin esta guarda `minAreaRect` revienta.
            return gris

        angulo = cv2.minAreaRect(coordenadas)[-1]
        # OpenCV devuelve el ángulo en [-90, 0); se lleva al rango pequeño alrededor
        # de cero que es el que interesa corregir.
        if angulo < -45:
            angulo += 90

        if abs(angulo) < _ANGULO_MINIMO_CORRECCION or abs(angulo) > _ANGULO_MAXIMO_CORRECCION:
            # Más de 15 grados no es un escaneo torcido: es otra orientación, y
            # rotarla como un desvío pequeño la dejaría peor.
            return gris

        alto, ancho = gris.shape[:2]
        matriz = cv2.getRotationMatrix2D((ancho / 2, alto / 2), angulo, 1.0)
        resultado: Any = cv2.warpAffine(
            gris, matriz, (ancho, alto),
            flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
        )
        return resultado
    except cv2.error:
        return gris


def _escalar(gris: Any) -> Any:  # noqa: ANN401
    """Lleva la imagen a una altura en la que Tesseract rinde bien."""
    import cv2

    alto, ancho = gris.shape[:2]
    if alto >= _ALTURA_OBJETIVO:
        if alto <= _ALTURA_MAXIMA:
            return gris
        factor = _ALTURA_MAXIMA / alto
    else:
        factor = _ALTURA_OBJETIVO / alto

    resultado: Any = cv2.resize(
        gris, (int(ancho * factor), int(alto * factor)),
        # CUBIC al ampliar (inventa píxeles suaves), AREA al reducir (promedia,
        # evitando el aliasing del texto).
        interpolation=cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA,
    )
    return resultado


def _binarizar(gris: Any) -> Any:  # noqa: ANN401
    """Umbral adaptativo gaussiano, bloque a bloque. Uno global (Otsu) funciona con
    un escaneo uniforme pero falla con una foto, donde un borde oscuro se vuelve negro."""
    import cv2

    resultado: Any = cv2.adaptiveThreshold(
        gris, maxValue=255, adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        thresholdType=cv2.THRESH_BINARY, blockSize=31, C=11,
    )
    return resultado
