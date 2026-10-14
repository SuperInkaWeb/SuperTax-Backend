"""Extracción (Fase 4): objetos de valor, políticas, perfil SUNAT, pipeline y visión.

Sin red, sin Groq y sin BD: la visión usa un cliente falso y el pipeline, motores
y dobles en memoria. El subproceso real (ProcessPoolExecutor) no se arranca aquí.
"""
import json

from src.modules.correos.domain import value_objects as vo
from src.modules.correos.domain.extraction import (
    CampoExtraido,
    Completitud,
    EstadoDeRevision,
    Estrategia,
    ResultadoDeEstrategia,
    clasificar,
    combinar,
    decidir_revision,
    es_suficiente_para_detenerse,
)
from src.modules.correos.infrastructure.extraction import vision_groq
from src.modules.correos.infrastructure.extraction.perfil_sunat import PerfilSunatArrendamiento
from src.modules.correos.infrastructure.extraction.pipeline import (
    ExtractorDeDocumentos,
    construir_datos,
)

# RUCs reales válidos (dígito verificador correcto).
_RUC_EMPRESA = "20131312955"
_RUC_PERSONA = "10426728554"


# ── Objetos de valor ─────────────────────────────────────────────────

def test_ruc_valida_digito_verificador():
    assert vo.Ruc.es_valido(_RUC_EMPRESA)
    assert vo.Ruc.es_valido(_RUC_PERSONA)
    assert not vo.Ruc.es_valido("20131312954")  # DV cambiado
    assert not vo.Ruc.es_valido("30131312955")  # prefijo inexistente


def test_ruc_interpretar_rescata_de_texto_sucio():
    assert str(vo.Ruc.interpretar(f"RUC: {_RUC_EMPRESA}")) == _RUC_EMPRESA
    assert str(vo.Ruc.interpretar(f"xx {_RUC_EMPRESA} yy")) == _RUC_EMPRESA
    assert vo.Ruc.interpretar("12345") is None


def test_periodo_normaliza_formatos():
    assert str(vo.PeriodoTributario.interpretar("03/2026")) == "202603"
    assert str(vo.PeriodoTributario.interpretar("2026-03")) == "202603"
    assert str(vo.PeriodoTributario.interpretar("marzo 2026")) == "202603"
    assert str(vo.PeriodoTributario.interpretar("202603")) == "202603"
    assert vo.PeriodoTributario.interpretar("13/2026") is None  # mes inválido


def test_importe_decimal_peruano_y_anglosajon():
    assert str(vo.Importe.interpretar("S/ 1,500.00")) == "1500.00"
    assert str(vo.Importe.interpretar("1.234,56")) == "1234.56"
    assert str(vo.Importe.interpretar("75.00")) == "75.00"
    assert vo.Importe.interpretar("6,500").cantidad == vo.Importe.interpretar("6500").cantidad


def test_fecha_de_pago_dia_mes_anio():
    assert str(vo.FechaDePago.interpretar("15/04/2026")) == "2026-04-15"
    assert str(vo.FechaDePago.interpretar("2026-04-15")) == "2026-04-15"
    assert vo.FechaDePago.interpretar("31/02/2026") is None  # fecha imposible


def test_numero_de_operacion_limpia_y_valida():
    # Conserva alfanuméricos y guiones (el nº de operación puede serlo); descarta el resto.
    assert str(vo.NumeroDeOperacion.interpretar("  987-654-321 ")) == "987-654-321"
    assert str(vo.NumeroDeOperacion.interpretar("987654321")) == "987654321"
    assert vo.NumeroDeOperacion.interpretar("12") is None  # demasiado corto


# ── Políticas de calidad ─────────────────────────────────────────────

def _campo(valor: str, conf: float, est=Estrategia.TEXTO_NATIVO) -> CampoExtraido:
    return CampoExtraido(valor, conf, est)


def test_combinar_toma_el_mejor_por_campo():
    r1 = ResultadoDeEstrategia(Estrategia.TEXTO_NATIVO, {"ruc_contribuyente": _campo("A", 0.98)})
    r2 = ResultadoDeEstrategia(
        Estrategia.OCR_LOCAL,
        {"ruc_contribuyente": _campo("B", 0.5), "importe_pagado": _campo("75.00", 0.8)},
    )
    combinado = combinar([r1, r2])
    assert combinado["ruc_contribuyente"].valor == "A"  # gana la mayor confianza
    assert combinado["importe_pagado"].valor == "75.00"


def test_clasificar_completo_parcial_vacio():
    assert clasificar({}) is Completitud.VACIO
    completo = {
        "ruc_contribuyente": _campo(_RUC_EMPRESA, 0.98),
        "periodo": _campo("202603", 0.96),
        "importe_pagado": _campo("75.00", 0.95),
    }
    assert clasificar(completo) is Completitud.COMPLETO
    del completo["importe_pagado"]
    assert clasificar(completo) is Completitud.PARCIAL


def test_decidir_revision_y_suficiencia():
    imprescindibles = {
        "ruc_contribuyente": _campo(_RUC_EMPRESA, 0.98),
        "periodo": _campo("202603", 0.96),
        "importe_pagado": _campo("75.00", 0.95),
    }
    res = ResultadoDeEstrategia(Estrategia.TEXTO_NATIVO, imprescindibles)
    assert es_suficiente_para_detenerse(res)
    assert decidir_revision(clasificar(imprescindibles), imprescindibles) is (
        EstadoDeRevision.NO_REQUERIDA
    )
    # Un imprescindible dudoso ya no basta para detenerse.
    imprescindibles["importe_pagado"] = _campo("75.00", 0.5)
    res_dudoso = ResultadoDeEstrategia(Estrategia.TEXTO_NATIVO, imprescindibles)
    assert not es_suficiente_para_detenerse(res_dudoso)
    assert decidir_revision(clasificar(imprescindibles), imprescindibles) is (
        EstadoDeRevision.PENDIENTE
    )


# ── Perfil SUNAT ─────────────────────────────────────────────────────

_CONSTANCIA = f"""\
CONSTANCIA DE PAGO
Formulario 1683 - Impuesto a la Renta de Primera Categoria
Arrendamiento de predio

RUC del Arrendador: {_RUC_EMPRESA}
Nombre / Razon Social: INVERSIONES LIMA SAC
RUC del Arrendatario: {_RUC_PERSONA}
Periodo Tributario: 03/2026
Fecha de Pago: 15/04/2026
Numero de Operacion: 987654321
Monto de Alquiler: S/ 1,500.00
Importe Pagado: S/ 75.00
"""


def test_perfil_reconoce_constancia():
    perfil = PerfilSunatArrendamiento()
    assert perfil.reconoce(_CONSTANCIA)
    assert not perfil.reconoce("Factura de luz del mes. Total a pagar: 90.00")


def test_perfil_extrae_campos_clave():
    campos = PerfilSunatArrendamiento().extraer_campos(_CONSTANCIA)
    assert _RUC_EMPRESA in campos["ruc_contribuyente"].valor
    assert "importe_pagado" in campos
    assert vo.PeriodoTributario.interpretar(campos["periodo"].valor) is not None


# ── Pipeline ─────────────────────────────────────────────────────────

def test_construir_datos_convierte_a_tipos():
    campos = {
        "ruc_contribuyente": _campo(_RUC_EMPRESA, 0.98),
        "periodo": _campo("03/2026", 0.96),
        "importe_pagado": _campo("S/ 75.00", 0.95),
        "fecha_de_pago": _campo("15/04/2026", 0.96),
    }
    datos = construir_datos([ResultadoDeEstrategia(Estrategia.TEXTO_NATIVO, campos)], uso_ia=False)
    assert datos.reconocido
    assert datos.ruc_contribuyente == _RUC_EMPRESA
    assert datos.periodo == "202603"
    assert datos.importe_pagado == "75.00"
    assert datos.fecha_de_pago == "2026-04-15"
    assert datos.completitud == Completitud.COMPLETO.value


class _MotorFalso:
    """Estrategia de prueba: devuelve campos fijos sin tocar librerías reales."""

    def __init__(self, nombre, costo, campos, *, admite=True):
        self.nombre = nombre
        self.costo_relativo = costo
        self._campos = campos
        self._admite = admite
        self.llamado = False

    def admite(self, mime):
        return self._admite

    def leer(self, contenido, mime, perfil):
        self.llamado = True
        return ResultadoDeEstrategia(self.nombre, dict(self._campos))


def test_pipeline_se_detiene_al_ser_suficiente():
    suficiente = {
        "ruc_contribuyente": _campo(_RUC_EMPRESA, 0.98, Estrategia.TEXTO_NATIVO),
        "periodo": _campo("202603", 0.96, Estrategia.TEXTO_NATIVO),
        "importe_pagado": _campo("75.00", 0.95, Estrategia.TEXTO_NATIVO),
    }
    barato = _MotorFalso(Estrategia.TEXTO_NATIVO, 10, suficiente)
    caro = _MotorFalso(Estrategia.OCR_LOCAL, 50, {})
    ExtractorDeDocumentos([caro, barato], PerfilSunatArrendamiento(), permitir_ia=False).ejecutar(
        b"x", "application/pdf", "c.pdf"
    )
    assert barato.llamado and not caro.llamado  # el barato bastó


def test_pipeline_omite_vision_sin_presupuesto():
    vision = _MotorFalso(Estrategia.VISION_IA, 100, {"importe_pagado": _campo("1.00", 0.9)})
    ExtractorDeDocumentos([vision], PerfilSunatArrendamiento(), permitir_ia=False).ejecutar(
        b"x", "image/png", "f.png"
    )
    assert not vision.llamado


# ── Visión IA (cliente falso) ────────────────────────────────────────

class _ClienteFalso:
    def __init__(self, contenido: str):
        self._contenido = contenido
        self.chat = self
        self.completions = self

    def create(self, **_):
        msg = type("M", (), {"content": self._contenido})()
        choice = type("C", (), {"message": msg})()
        return type("R", (), {"choices": [choice]})()


def test_vision_lee_constancia_valida():
    cuerpo = json.dumps({
        "es_constancia_sunat": True,
        "ruc_contribuyente": {"valor": _RUC_EMPRESA, "confianza": 0.95},
        "importe_pagado": {"valor": "75.00", "confianza": 0.9},
    })
    motor = vision_groq.VisionIAGroq(_ClienteFalso(cuerpo))
    res = motor.leer(b"fakepng", "image/png", None)
    assert res.tuvo_exito
    assert res.campos["ruc_contribuyente"].valor == _RUC_EMPRESA
    # Confianza recortada al techo (0.95 declarado → 0.90).
    assert res.campos["ruc_contribuyente"].confianza == vision_groq.TECHO_DE_CONFIANZA


def test_vision_documento_no_reconocido():
    cuerpo = json.dumps({"es_constancia_sunat": False})
    res = vision_groq.VisionIAGroq(_ClienteFalso(cuerpo)).leer(b"x", "image/png", None)
    assert not res.tuvo_exito and res.error == "documento_no_reconocido"


def test_vision_json_invalido_no_revienta():
    res = vision_groq.VisionIAGroq(_ClienteFalso("no soy json")).leer(b"x", "image/png", None)
    assert not res.tuvo_exito and res.campos == {}


def test_crear_cliente_sin_api_key_es_none():
    assert vision_groq.crear_cliente(None) is None
    assert vision_groq.crear_cliente("") is None
