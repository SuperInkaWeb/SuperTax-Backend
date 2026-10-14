"""
Orquestador de la extracción.

Prueba los motores de lectura por orden de costo y para en cuanto el resultado es
suficiente. Aunque llegue al final, COMBINA todos los resultados en vez de quedarse
con el último: es habitual que el texto nativo lea el RUC perfecto y falle en un
importe dentro de una imagen, donde el OCR sí acierta. La IA de visión solo se
intenta si el presupuesto del escaneo lo permite (degradar a los motores gratuitos
da un resultado parcial corregible, que vale más que ninguno).

`extraer_en_subproceso` es el punto de entrada que corre en el subproceso efímero:
recibe bytes + configuración (todo picklable) y devuelve un dict serializable, sin
tocar la BD ni R2. El subproceso muere al terminar y devuelve su RAM al SO.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from src.modules.correos.domain import value_objects as vo
from src.modules.correos.domain.extraction import (
    Estrategia,
    ResultadoDeEstrategia,
    clasificar,
    combinar,
    confianza_global,
    decidir_revision,
    es_suficiente_para_detenerse,
    estrategia_dominante,
)
from src.modules.correos.infrastructure.extraction import vision_groq
from src.modules.correos.infrastructure.extraction.motores import (
    OcrLocal,
    TablasDePdf,
    TextoNativoDePdf,
)
from src.modules.correos.infrastructure.extraction.perfil_sunat import (
    NOMBRE_DEL_PERFIL,
    PerfilSunatArrendamiento,
    limpiar_nombre,
    normalizar_tipo,
)

logger = logging.getLogger("correos.extraction.pipeline")


@dataclass(slots=True)
class DatosExtraidos:
    """
    Resultado de extraer un adjunto, serializable (solo tipos simples) para cruzar la
    frontera del subproceso. Los importes y fechas van como texto; el servicio que
    persiste los convierte a Decimal/date al construir el modelo.
    """

    reconocido: bool = False
    perfil: str = NOMBRE_DEL_PERFIL
    ruc_contribuyente: str | None = None
    nombre_contribuyente: str = ""
    ruc_inquilino: str | None = None
    nombre_inquilino: str = ""
    tipo_doc_inquilino: str = ""
    tipo_de_bien: str = ""
    periodo: str | None = None
    fecha_de_pago: str | None = None  # ISO (YYYY-MM-DD)
    numero_de_operacion: str | None = None
    monto_alquiler: str | None = None
    tributo_resultante: str | None = None
    importe_pagado: str | None = None
    intereses_moratorios: str | None = None
    moneda: str = "PEN"
    campos_crudos: dict[str, str] = field(default_factory=dict)
    confianza_por_campo: dict[str, float] = field(default_factory=dict)
    completitud: str = "empty"
    estado_de_revision: str = "not_required"
    estrategia_usada: str | None = None
    duracion_ms: int = 0
    uso_ia: bool = False


class ExtractorDeDocumentos:
    """Pipeline de extracción de un adjunto (sync)."""

    def __init__(
        self, estrategias: list[Any], perfil: PerfilSunatArrendamiento, *, permitir_ia: bool
    ) -> None:
        # Se ordenan por costo al construir: el orden es propiedad del pipeline.
        self._estrategias = sorted(estrategias, key=lambda e: e.costo_relativo)
        self._perfil = perfil
        self._permitir_ia = permitir_ia

    def ejecutar(self, contenido: bytes, mime: str, nombre: str) -> DatosExtraidos:
        resultados, uso_ia = self._recorrer(contenido, mime)
        if not resultados:
            logger.info("ningún motor admitió el documento (%s)", nombre)
            return DatosExtraidos(uso_ia=uso_ia)
        return construir_datos(resultados, uso_ia=uso_ia)

    def _recorrer(self, contenido: bytes, mime: str) -> tuple[list[ResultadoDeEstrategia], bool]:
        resultados: list[ResultadoDeEstrategia] = []
        uso_ia = False
        for estrategia in self._estrategias:
            if not estrategia.admite(mime):
                continue
            if estrategia.nombre is Estrategia.VISION_IA and not self._permitir_ia:
                continue
            resultado = estrategia.leer(contenido, mime, self._perfil)
            resultados.append(resultado)
            if estrategia.nombre is Estrategia.VISION_IA:
                uso_ia = True
            if resultado.tuvo_exito and es_suficiente_para_detenerse(resultado):
                # Los imprescindibles están y son fiables: seguir solo añadiría costo.
                break
        return resultados, uso_ia


def construir_datos(
    resultados: list[ResultadoDeEstrategia], *, uso_ia: bool
) -> DatosExtraidos:
    """Combina los resultados de los motores en un `DatosExtraidos` listo para persistir."""
    campos = combinar(resultados)
    completitud = clasificar(campos)
    estado = decidir_revision(completitud, campos)
    dominante = estrategia_dominante(campos)
    logger.debug(
        "extracción: completitud=%s revisión=%s confianza=%.3f motores=%d",
        completitud.value, estado.value, confianza_global(campos), len(resultados),
    )

    def crudo(nombre: str) -> str | None:
        campo = campos.get(nombre)
        return campo.valor if campo else None

    importe_pagado = vo.Importe.interpretar(crudo("importe_pagado"))
    monto_alquiler = vo.Importe.interpretar(crudo("monto_alquiler"))
    tributo = vo.Importe.interpretar(crudo("tributo_resultante"))
    intereses = vo.Importe.interpretar(crudo("intereses_moratorios"))
    moneda = next((i.moneda for i in (importe_pagado, monto_alquiler) if i is not None), "PEN")

    ruc_c = vo.Ruc.interpretar(crudo("ruc_contribuyente"))
    ruc_i = vo.Ruc.interpretar(crudo("ruc_inquilino"))
    periodo = vo.PeriodoTributario.interpretar(crudo("periodo"))
    fecha = vo.FechaDePago.interpretar(crudo("fecha_de_pago"))
    operacion = vo.NumeroDeOperacion.interpretar(crudo("numero_de_operacion"))

    return DatosExtraidos(
        reconocido=completitud.value != "empty",
        ruc_contribuyente=str(ruc_c) if ruc_c else None,
        nombre_contribuyente=limpiar_nombre(crudo("nombre_contribuyente")),
        ruc_inquilino=str(ruc_i) if ruc_i else None,
        nombre_inquilino=limpiar_nombre(crudo("nombre_inquilino")),
        tipo_doc_inquilino=normalizar_tipo(crudo("tipo_doc_inquilino")),
        tipo_de_bien=normalizar_tipo(crudo("tipo_de_bien")),
        periodo=str(periodo) if periodo else None,
        fecha_de_pago=str(fecha) if fecha else None,
        numero_de_operacion=str(operacion) if operacion else None,
        monto_alquiler=str(monto_alquiler) if monto_alquiler else None,
        tributo_resultante=str(tributo) if tributo else None,
        importe_pagado=str(importe_pagado) if importe_pagado else None,
        intereses_moratorios=str(intereses) if intereses else None,
        moneda=moneda,
        campos_crudos={n: c.valor for n, c in campos.items()},
        confianza_por_campo={n: round(c.confianza, 3) for n, c in campos.items()},
        completitud=completitud.value,
        estado_de_revision=estado.value,
        estrategia_usada=dominante.value if dominante else None,
        duracion_ms=sum(r.duracion_ms for r in resultados),
        uso_ia=uso_ia,
    )


# ── Entrada del subproceso efímero ───────────────────────────────────

_GRATUITAS: Final = (TextoNativoDePdf, TablasDePdf)


def extraer_en_subproceso(payload: dict) -> dict:
    """
    Punto de entrada del `ProcessPoolExecutor` (debe ser una función de módulo,
    picklable). Monta los motores, extrae y devuelve un dict serializable.

    payload: contenido(bytes), mime, nombre, permitir_ia(bool), tesseract_cmd(str),
    groq_api_key(str|None), groq_model(str).
    """
    perfil = PerfilSunatArrendamiento()
    estrategias: list[Any] = [cls() for cls in _GRATUITAS]
    estrategias.append(OcrLocal(tesseract_cmd=payload.get("tesseract_cmd", "tesseract")))

    permitir_ia = bool(payload.get("permitir_ia"))
    cliente = vision_groq.crear_cliente(payload.get("groq_api_key")) if permitir_ia else None
    if cliente is not None:
        modelo = payload.get("groq_model") or vision_groq.MODELO_POR_DEFECTO
        estrategias.append(vision_groq.VisionIAGroq(cliente, modelo=modelo))

    extractor = ExtractorDeDocumentos(estrategias, perfil, permitir_ia=permitir_ia)
    datos = extractor.ejecutar(
        payload["contenido"], payload["mime"], payload.get("nombre", "")
    )
    return asdict(datos)
