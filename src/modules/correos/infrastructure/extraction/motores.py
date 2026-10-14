"""
Motores de lectura gratuitos: texto nativo de PDF, tablas de PDF y OCR local.

Versión SYNC (la del standalone era async): el pipeline corre en un subproceso
efímero, no en el event loop. Ninguno lanza hacia arriba —un PDF corrupto devuelve
un resultado con `error` y el pipeline prueba el siguiente motor— para que un solo
documento no tumbe el escaneo entero. Las librerías pesadas (pymupdf, pdfplumber,
pytesseract) se importan de forma perezosa dentro de cada motor.
"""
from __future__ import annotations

import io
import logging
import time
from typing import Any, Final

from src.modules.correos.domain.extraction import (
    CampoExtraido,
    Estrategia,
    ResultadoDeEstrategia,
)
from src.modules.correos.infrastructure.extraction.perfil_sunat import PerfilSunatArrendamiento

logger = logging.getLogger("correos.extraction.motores")

# Las constancias tienen una o dos páginas; estos topes acotan el costo de un
# documento inesperadamente largo.
_MAXIMO_PAGINAS: Final = 10
_MINIMO_CARACTERES_UTILES: Final = 80  # por debajo, el "texto nativo" es ruido
_DPI_RASTERIZADO: Final = 300
_MAXIMO_PAGINAS_OCR: Final = 5
_IDIOMAS_OCR: Final = "spa+eng"
# PSM 6 ("un bloque uniforme de texto") es el modo adecuado para un formulario.
_CONFIG_OCR: Final = "--oem 3 --psm 6"
# Tesseract marca con -1 las palabras no reconocidas; incluirlas hundiría la media.
_CONFIANZA_NO_DISPONIBLE: Final = -1


def _mime_es_pdf(mime: str) -> bool:
    return mime == "application/pdf"


def _mime_es_imagen(mime: str) -> bool:
    return mime.startswith("image/")


def _ms(inicio: float) -> int:
    return int((time.perf_counter() - inicio) * 1000)


def con_estrategia(
    campos: dict[str, CampoExtraido], estrategia: Estrategia
) -> dict[str, CampoExtraido]:
    """Marca cada campo con el motor que lo produjo (el perfil pone un valor por
    defecto); así luego se sabe qué motor acertó con cada campo."""
    return {
        nombre: CampoExtraido(campo.valor, campo.confianza, estrategia)
        for nombre, campo in campos.items()
    }


class TextoNativoDePdf:
    """Extrae la capa de texto que el PDF ya lleva incorporada (motor más barato)."""

    nombre: Final = Estrategia.TEXTO_NATIVO
    costo_relativo: Final = 10

    def admite(self, mime: str) -> bool:
        return _mime_es_pdf(mime)

    def leer(
        self, contenido: bytes, mime: str, perfil: PerfilSunatArrendamiento
    ) -> ResultadoDeEstrategia:
        inicio = time.perf_counter()
        try:
            texto = self._extraer_texto(contenido)
        except Exception as exc:  # noqa: BLE001 - frontera del motor
            logger.warning("pdf_texto_nativo_fallo: %s", type(exc).__name__)
            return ResultadoDeEstrategia(self.nombre, duracion_ms=_ms(inicio),
                                         error=type(exc).__name__)

        if len(texto.strip()) < _MINIMO_CARACTERES_UTILES:
            # PDF escaneado: no hay capa de texto. No es error, es la señal de OCR.
            return ResultadoDeEstrategia(self.nombre, texto_crudo=texto,
                                         duracion_ms=_ms(inicio), error="sin_capa_de_texto")

        campos = perfil.extraer_campos(texto) if perfil.reconoce(texto) else {}
        return ResultadoDeEstrategia(
            self.nombre, campos=con_estrategia(campos, self.nombre),
            texto_crudo=texto, duracion_ms=_ms(inicio),
        )

    @staticmethod
    def _extraer_texto(contenido: bytes) -> str:
        import pymupdf

        partes: list[str] = []
        with pymupdf.open(stream=contenido, filetype="pdf") as doc:
            for indice, pagina in enumerate(doc):
                if indice >= _MAXIMO_PAGINAS:
                    break
                partes.append(pagina.get_text("text"))
        return "\n".join(partes)


class TablasDePdf:
    """
    Lee las tablas del PDF y las aplana a texto. Existe porque el texto nativo
    devuelve el contenido en el orden en que está dibujado, que no siempre es el de
    lectura: una etiqueta puede acabar lejos de su valor. pdfplumber reconstruye
    filas y columnas y los vuelve a unir.
    """

    nombre: Final = Estrategia.TABLAS_PDF
    costo_relativo: Final = 20

    def admite(self, mime: str) -> bool:
        return _mime_es_pdf(mime)

    def leer(
        self, contenido: bytes, mime: str, perfil: PerfilSunatArrendamiento
    ) -> ResultadoDeEstrategia:
        inicio = time.perf_counter()
        try:
            texto = self._extraer_tablas(contenido)
        except Exception as exc:  # noqa: BLE001 - frontera del motor
            logger.warning("pdf_tablas_fallo: %s", type(exc).__name__)
            return ResultadoDeEstrategia(self.nombre, duracion_ms=_ms(inicio),
                                         error=type(exc).__name__)

        if not texto.strip():
            return ResultadoDeEstrategia(self.nombre, duracion_ms=_ms(inicio), error="sin_tablas")

        return ResultadoDeEstrategia(
            self.nombre, campos=con_estrategia(perfil.extraer_campos(texto), self.nombre),
            texto_crudo=texto, duracion_ms=_ms(inicio),
        )

    @staticmethod
    def _extraer_tablas(contenido: bytes) -> str:
        import pdfplumber

        filas: list[str] = []
        with pdfplumber.open(io.BytesIO(contenido)) as doc:
            for indice, pagina in enumerate(doc.pages):
                if indice >= _MAXIMO_PAGINAS:
                    break
                for tabla in pagina.extract_tables() or []:
                    for fila in tabla:
                        celdas = [str(c).strip() for c in fila if c]
                        if celdas:
                            # ": " deja etiqueta y valor adyacentes, como esperan
                            # los patrones del perfil.
                            filas.append(": ".join(celdas))
        return "\n".join(filas)


class OcrLocal:
    """Tesseract sobre la imagen preprocesada. Último motor gratuito antes de la IA."""

    nombre: Final = Estrategia.OCR_LOCAL
    costo_relativo: Final = 50

    def __init__(self, tesseract_cmd: str = "tesseract") -> None:
        self._tesseract_cmd = tesseract_cmd

    def admite(self, mime: str) -> bool:
        return _mime_es_pdf(mime) or _mime_es_imagen(mime)

    def leer(
        self, contenido: bytes, mime: str, perfil: PerfilSunatArrendamiento
    ) -> ResultadoDeEstrategia:
        inicio = time.perf_counter()
        try:
            texto, confianza = self._reconocer(contenido, mime)
        except Exception as exc:  # noqa: BLE001 - frontera del motor
            logger.warning("ocr_fallo: %s", type(exc).__name__)
            return ResultadoDeEstrategia(self.nombre, duracion_ms=_ms(inicio),
                                         error=type(exc).__name__)

        if not texto.strip():
            return ResultadoDeEstrategia(self.nombre, duracion_ms=_ms(inicio), error="sin_texto")

        campos = perfil.extraer_campos(texto)
        return ResultadoDeEstrategia(
            self.nombre, campos=self._ajustar_confianza(campos, confianza),
            texto_crudo=texto, duracion_ms=_ms(inicio),
        )

    def _reconocer(self, contenido: bytes, mime: str) -> tuple[str, float]:
        imagenes = self._rasterizar_pdf(contenido) if _mime_es_pdf(mime) else [contenido]
        textos: list[str] = []
        confianzas: list[float] = []
        for crudo in imagenes:
            texto, confianza = self._reconocer_una(crudo)
            if texto.strip():
                textos.append(texto)
                confianzas.append(confianza)
        media = sum(confianzas) / len(confianzas) if confianzas else 0.0
        return "\n".join(textos), media

    def _reconocer_una(self, contenido: bytes) -> tuple[str, float]:
        import pytesseract
        from PIL import Image

        from src.modules.correos.infrastructure.extraction.preprocesado import preparar_para_ocr

        pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd
        imagen = Image.fromarray(preparar_para_ocr(contenido))
        datos: dict[str, Any] = pytesseract.image_to_data(
            imagen, lang=_IDIOMAS_OCR, config=_CONFIG_OCR,
            output_type=pytesseract.Output.DICT,
        )

        palabras: list[str] = []
        confianzas: list[float] = []
        for texto, confianza in zip(datos["text"], datos["conf"], strict=True):
            if not str(texto).strip():
                continue
            palabras.append(str(texto))
            valor = float(confianza)
            if valor > _CONFIANZA_NO_DISPONIBLE:
                confianzas.append(valor / 100.0)

        media = sum(confianzas) / len(confianzas) if confianzas else 0.0
        return " ".join(palabras), media

    @staticmethod
    def _rasterizar_pdf(contenido: bytes) -> list[bytes]:
        import pymupdf

        paginas: list[bytes] = []
        with pymupdf.open(stream=contenido, filetype="pdf") as doc:
            for indice, pagina in enumerate(doc):
                if indice >= _MAXIMO_PAGINAS_OCR:
                    break
                paginas.append(pagina.get_pixmap(dpi=_DPI_RASTERIZADO).tobytes("png"))
        return paginas

    def _ajustar_confianza(
        self, campos: dict[str, CampoExtraido], confianza_del_ocr: float
    ) -> dict[str, CampoExtraido]:
        """Combina la confianza del patrón con la de Tesseract multiplicándolas: si el
        OCR leyó mal la página, da igual lo específico que fuera el patrón. El producto
        refleja que ambas condiciones tienen que cumplirse."""
        return {
            nombre: CampoExtraido(
                valor=campo.valor,
                confianza=round(campo.confianza * confianza_del_ocr, 3),
                estrategia=self.nombre,
            )
            for nombre, campo in campos.items()
        }
