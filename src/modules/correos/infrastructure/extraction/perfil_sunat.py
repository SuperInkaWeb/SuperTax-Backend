"""
Perfil de extracción: constancia SUNAT de pago de arrendamiento (formulario 1683,
impuesto a la renta de primera categoría).

Localiza los campos del formulario en el texto de un documento, sea cual sea el
motor que lo haya leído. No conoce PDFs, píxeles ni OCR.

Decisiones de diseño
    1. Las etiquetas se buscan con tolerancia a las confusiones típicas del OCR
       (0/O, 1/l/i, 5/S, 8/B, 2/Z) y a las tildes perdidas. La tolerancia se aplica
       SOLO a las etiquetas: en los valores, un 0 y una O significan cosas distintas.
    2. Cada campo tiene varios patrones, de más específico a más general, cada uno
       con su confianza.
    3. El valor pasa siempre por su objeto de valor: un RUC que no supere el dígito
       verificador se descarta aunque el patrón haya coincidido.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from src.modules.correos.domain.extraction import CampoExtraido, Estrategia
from src.modules.correos.domain.value_objects import (
    FechaDePago,
    Importe,
    NumeroDeOperacion,
    PeriodoTributario,
    Ruc,
)

NOMBRE_DEL_PERFIL: Final = "sunat_arrendamiento"


# ── Tolerancia a las confusiones del OCR ─────────────────────────────

_CONFUSIONES: Final[dict[str, str]] = {
    "a": "aá4@", "b": "b8", "e": "eé3", "g": "g9", "i": "ií1l|", "l": "l1i|",
    "n": "nñ", "o": "oó0", "s": "s5$", "t": "t7", "u": "uú", "z": "z2",
}


def _tolerante(palabra: str) -> str:
    """
    Reconoce una palabra aunque el OCR haya cambiado alguna letra por su cifra
    parecida. Los espacios se vuelven `\\s*`: el OCR tanto los pierde como los
    inventa dentro de una misma etiqueta.
    """
    partes: list[str] = []
    for caracter in palabra:
        if caracter == " ":
            partes.append(r"\s*")
        elif caracter in _CONFUSIONES:
            partes.append(f"[{re.escape(_CONFUSIONES[caracter])}]")
        else:
            partes.append(re.escape(caracter))
    return "".join(partes)


# Separador entre etiqueta y valor: los dos puntos se pierden y quedan espacios.
_SEP: Final = r"\s*[:.\-]?\s*"


@dataclass(frozen=True, slots=True)
class _Patron:
    """Una forma de encontrar un campo, con la confianza que merece."""

    expresion: re.Pattern[str]
    confianza: float


def _compilar(patron: str, confianza: float) -> _Patron:
    return _Patron(re.compile(patron, re.IGNORECASE | re.MULTILINE), confianza)


# ── Fragmentos reutilizados ──────────────────────────────────────────

_ARRENDADOR: Final = (
    f"(?:{_tolerante('arrendador')}|{_tolerante('contribuyente')}|{_tolerante('declarante')})"
)
_ARRENDATARIO: Final = f"(?:{_tolerante('arrendatario')}|{_tolerante('inquilino')})"
_DEL: Final = rf"(?:\s*{_tolerante('de')}[l1i]?\s*)?"

_NOMBRE_BASE: Final = (
    f"(?:{_tolerante('nombres')}?|{_tolerante('apellidos y nombres')}"
    f"|{_tolerante('razon social')}|{_tolerante('razon socia')}[l1i])"
)
_ETIQUETA_NOMBRE: Final = rf"{_NOMBRE_BASE}(?:\s*[/y]\s*{_NOMBRE_BASE})?"

_DIGITOS_RUC: Final = r"(\d[\d\s\-]{9,16})"
_FECHA: Final = r"(\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4})"
# Valor monetario: símbolo opcional y asteriscos de enmascarado —el Banco de la
# Nación imprime "S/ *********326.00"— seguidos del número.
_MONTO: Final = r"((?:s\s*/\.?|us\$|\$)?[\s*]*\d[\d.,]{0,17})"


# ── Patrones por campo (el grupo 1 es siempre el valor buscado) ──────

_PATRONES: Final[dict[str, tuple[_Patron, ...]]] = {
    "ruc_contribuyente": (
        _compilar(rf"{_tolerante('ruc')}{_DEL}{_ARRENDADOR}{_SEP}{_DIGITOS_RUC}", 0.98),
        _compilar(rf"{_ARRENDADOR}{_SEP}[^\n]{{0,40}}?(\d{{11}})", 0.90),
        _compilar(rf"\b{_tolerante('ruc')}{_SEP}{_DIGITOS_RUC}", 0.85),
    ),
    "nombre_contribuyente": (
        _compilar(rf"{_ETIQUETA_NOMBRE}{_DEL}{_ARRENDADOR}{_SEP}([^\n]{{4,120}})", 0.94),
        _compilar(rf"{_ETIQUETA_NOMBRE}\s*[:]\s*([^\n]{{4,120}})", 0.90),
        _compilar(rf"{_ETIQUETA_NOMBRE}\s{{2,}}([A-ZÁÉÍÓÚÑ][^\n]{{3,120}})", 0.84),
    ),
    "ruc_inquilino": (
        _compilar(
            rf"(?:{_tolerante('ruc')}|{_tolerante('doc')}){_DEL}{_ARRENDATARIO}{_SEP}{_DIGITOS_RUC}",
            0.96,
        ),
        _compilar(rf"{_ARRENDATARIO}{_SEP}[^\n]{{0,40}}?(\d{{11}})", 0.88),
    ),
    "nombre_inquilino": (
        _compilar(rf"{_ETIQUETA_NOMBRE}{_DEL}{_ARRENDATARIO}{_SEP}([^\n]{{4,120}})", 0.94),
        # El valor va en la MISMA línea de la etiqueta y no empieza por un tipo de
        # documento: "Documento Inquilino: RUC - 20..." no es el nombre.
        _compilar(
            rf"{_ARRENDATARIO}[ \t]*:[ \t]*"
            r"(?!(?:r\.?u\.?c|dni|c\.?e|pasaporte|\d))([A-ZÁÉÍÓÚÑ][^\n]{3,120})",
            0.84,
        ),
    ),
    "periodo": (
        _compilar(
            rf"{_tolerante('periodo')}(?:\s*{_tolerante('tributario')})?{_SEP}"
            r"([0-9]{4}\s*[-/]?\s*[0-9]{2}|[0-9]{2}\s*[-/]?\s*[0-9]{4}|[a-zñáéíóú]+\s+(?:de\s+)?[0-9]{4})",
            0.96,
        ),
        _compilar(
            rf"{_tolerante('mes')}(?:\s*[yY]?\s*{_tolerante('ano')})?{_SEP}"
            r"([0-9]{2}\s*[-/]\s*[0-9]{4})",
            0.88,
        ),
    ),
    "fecha_de_pago": (
        _compilar(rf"{_tolerante('fecha')}{_DEL}{_tolerante('pago')}{_SEP}{_FECHA}", 0.96),
        _compilar(
            rf"{_tolerante('fecha')}{_DEL}(?:{_tolerante('presentacion')}|{_tolerante('emision')}){_SEP}{_FECHA}",
            0.86,
        ),
    ),
    "numero_de_operacion": (
        # El valor debe empezar por un dígito: así "Operación SUNAT: 77..." no
        # captura "SUNAT". Se salta además un calificador como "SUNAT" o "bancaria".
        _compilar(
            rf"(?:{_tolerante('nro')}|{_tolerante('numero')}|{_tolerante('num')}|n)?\.?\s*"
            rf"{_DEL}{_tolerante('operacion')}(?:\s*[a-z]+)?{_SEP}([0-9][0-9\-]{{3,29}})",
            0.95,
        ),
        _compilar(
            rf"(?:{_tolerante('nro')}|{_tolerante('numero')})?\.?\s*"
            rf"{_DEL}{_tolerante('orden')}{_SEP}([0-9][0-9\-]{{3,29}})",
            0.82,
        ),
    ),
    "monto_alquiler": (
        _compilar(rf"{_tolerante('monto')}{_DEL}{_tolerante('alquiler')}{_SEP}{_MONTO}", 0.95),
    ),
    "tributo_resultante": (
        # Anclado a "resultante": "Tributo: 3011" es el código del tributo, no un importe.
        _compilar(rf"{_tolerante('tributo')}\s*{_tolerante('resultante')}{_SEP}{_MONTO}", 0.95),
    ),
    "importe_pagado": (
        _compilar(
            rf"{_tolerante('importe')}\s*"
            rf"(?:{_tolerante('pagado')}|{_tolerante('total')}){_SEP}{_MONTO}",
            0.95,
        ),
        _compilar(rf"\b{_tolerante('importe')}{_SEP}{_MONTO}", 0.80),
    ),
    "intereses_moratorios": (
        _compilar(
            rf"{_tolerante('interes')}[a-z]*\s*{_tolerante('moratorio')}[a-z]*{_SEP}{_MONTO}",
            0.9,
        ),
    ),
    "tipo_de_bien": (
        _compilar(
            rf"{_tolerante('tipo')}\s*{_DEL}{_tolerante('bien')}{_SEP}([A-Za-z]{{3,20}})", 0.9
        ),
    ),
    "tipo_doc_inquilino": (
        _compilar(
            rf"{_tolerante('inquilino')}{_SEP}(?:\d{{2}}\s*[-/]?\s*)?"
            r"(r\.?\s*u\.?\s*c\.?|dni|c\.?\s*e\.?|pasaporte)",
            0.88,
        ),
    ),
}

# Validador por campo. El patrón dice dónde mirar; esto dice si lo que hay es real.
_VALIDADORES: Final = {
    "ruc_contribuyente": Ruc.interpretar,
    "ruc_inquilino": Ruc.interpretar,
    "periodo": PeriodoTributario.interpretar,
    "fecha_de_pago": FechaDePago.interpretar,
    "numero_de_operacion": NumeroDeOperacion.interpretar,
    "monto_alquiler": Importe.interpretar,
    "tributo_resultante": Importe.interpretar,
    "importe_pagado": Importe.interpretar,
    "intereses_moratorios": Importe.interpretar,
}

# Penalización cuando el patrón coincidió pero el valor no superó su validador. El
# dato se conserva (suele estar casi bien) pero queda marcado para revisión.
_PENALIZACION_SIN_VALIDAR: Final = 0.45

# Señales de que el documento es una constancia de arrendamiento. Se exigen dos:
# "1683" por sí solo aparece en cualquier correo de la entidad. Se cubren variantes
# reales de SUNAT SOL, el Banco de la Nación y pagalo.pe.
_SENALES: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        _tolerante("arrendamiento"),
        r"\b1683\b",
        r"\b3011\b",  # código del tributo "renta de 1ra categoría"
        _tolerante("impuesto a la renta"),
        _tolerante("constancia de pago"),
        _tolerante("primera categoria"),
        r"1ra\s*\.?\s*categor",
        _tolerante("recibo por arrendamiento"),
        _tolerante("renta de primera"),
    )
)
_SENALES_MINIMAS: Final = 2

_LONGITUD_MAXIMA_NOMBRE: Final = 120


class PerfilSunatArrendamiento:
    """Constancia de pago del impuesto a la renta de primera categoría."""

    nombre: Final = NOMBRE_DEL_PERFIL

    def reconoce(self, texto: str) -> bool:
        normalizado = _normalizar(texto)
        encontradas = sum(1 for senal in _SENALES if senal.search(normalizado))
        return encontradas >= _SENALES_MINIMAS

    def extraer_campos(self, texto: str) -> dict[str, CampoExtraido]:
        campos: dict[str, CampoExtraido] = {}
        for nombre, patrones in _PATRONES.items():
            campo = self._buscar(nombre, patrones, texto)
            if campo is not None:
                campos[nombre] = campo
        return campos

    def _buscar(
        self, nombre: str, patrones: tuple[_Patron, ...], texto: str
    ) -> CampoExtraido | None:
        """
        Prueba los patrones en orden y devuelve el primero cuyo valor supere el
        validador. Si ninguno valida pero alguno coincidió, se devuelve con confianza
        penalizada: perder el dato obligaría a transcribirlo entero a mano, cuando lo
        habitual es que solo haya que corregir un dígito.
        """
        mejor_sin_validar: CampoExtraido | None = None
        validador = _VALIDADORES.get(nombre)

        for patron in patrones:
            coincidencia = patron.expresion.search(texto)
            if coincidencia is None:
                continue

            valor = coincidencia.group(1).strip()
            if not valor:
                continue

            if validador is None:
                # Campos de texto libre (nombres): no hay nada que validar.
                return CampoExtraido(valor, patron.confianza, Estrategia.TEXTO_NATIVO)

            if validador(valor) is not None:
                return CampoExtraido(valor, patron.confianza, Estrategia.TEXTO_NATIVO)

            if mejor_sin_validar is None:
                mejor_sin_validar = CampoExtraido(
                    valor,
                    round(patron.confianza * _PENALIZACION_SIN_VALIDAR, 3),
                    Estrategia.TEXTO_NATIVO,
                )

        return mejor_sin_validar


def _normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios colapsados, para comparar."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_tildes)


def limpiar_nombre(crudo: str | None) -> str:
    """
    Recorta la basura que el OCR arrastra detrás de un nombre: lo típico es que la
    siguiente etiqueta se pegue al valor ("JUAN PEREZ RUC 10..."), así que se corta
    en el primer indicio de que empieza otro campo.
    """
    if not crudo:
        return ""
    corte = re.split(
        r"\s{3,}|\s+(?:ruc|periodo|fecha|importe|monto|operacion)\b",
        crudo,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return re.sub(r"[\s:;,.\-]+$", "", corte.strip())[:_LONGITUD_MAXIMA_NOMBRE]


def normalizar_tipo(crudo: str | None) -> str:
    """Normaliza un código corto a mayúsculas sin puntos ni espacios ("R.U.C" → "RUC")."""
    if not crudo:
        return ""
    return re.sub(r"[.\s]+", "", crudo).upper()[:40]
