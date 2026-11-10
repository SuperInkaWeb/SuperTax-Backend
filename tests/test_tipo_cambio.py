"""
Tests del tipo de cambio (requerimiento 2.1): parseo del BCRP, normalización a la
forma de SUNAT (desfase +1 día hábil, verificado contra el histórico real) y
upsert idempotente de la tabla global.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from src.modules.sire.infrastructure.models import TipoCambioModel
from src.modules.sire.infrastructure.tipo_cambio import bcrp, repositorio, sincronizador
from src.modules.sire.infrastructure.tipo_cambio.bcrp import CotizacionSbs, parse_respuesta
from src.modules.sire.infrastructure.tipo_cambio.normalizador import (
    normalizar_a_forma_sunat,
)

# Respuesta real del BCRP (serie SBS) para la primera semana hábil de enero 2019.
# config.series: [Compra, Venta]; values alineados a ese orden.
_BCRP_ENE_2019 = {
    "config": {
        "series": [
            {"name": "Tipo de cambio - TC Sistema bancario SBS (S/ por US$) - Compra", "dec": "3"},
            {"name": "Tipo de cambio - TC Sistema bancario SBS (S/ por US$) - Venta", "dec": "3"},
        ]
    },
    "periods": [
        {"name": "02.Ene.19", "values": ["3.369", "3.373"]},
        {"name": "03.Ene.19", "values": ["3.368", "3.371"]},
        {"name": "04.Ene.19", "values": ["3.356", "3.36"]},
        {"name": "07.Ene.19", "values": ["3.349", "3.353"]},
        {"name": "08.Ene.19", "values": ["3.347", "3.35"]},
    ],
}


def test_parse_respuesta_mapea_por_nombre_no_por_posicion():
    """El BCRP reordena las series; el parseo debe guiarse por el NOMBRE. Aquí las
    series vienen Venta primero y aun así compra/venta deben quedar bien."""
    data = {
        "config": {
            "series": [
                {"name": "...SBS... - Venta", "dec": "3"},
                {"name": "...SBS... - Compra", "dec": "3"},
            ]
        },
        "periods": [{"name": "02.Ene.19", "values": ["3.373", "3.369"]}],
    }
    cotizaciones = parse_respuesta(data)
    assert cotizaciones == [
        CotizacionSbs(fecha=date(2019, 1, 2), compra=Decimal("3.369"), venta=Decimal("3.373"))
    ]


def test_parse_respuesta_omite_dias_sin_dato():
    data = {
        "config": {"series": [{"name": "Compra"}, {"name": "Venta"}]},
        "periods": [
            {"name": "02.Ene.19", "values": ["n.d.", "n.d."]},
            {"name": "03.Ene.19", "values": ["3.368", "3.371"]},
        ],
    }
    cotizaciones = parse_respuesta(data)
    assert [c.fecha for c in cotizaciones] == [date(2019, 1, 3)]


def test_normalizador_reproduce_el_tc_de_sunat():
    """REGRESIÓN CLAVE: a partir de las cotizaciones SBS (días hábiles) del BCRP, la
    forma de SUNAT para cada día calendario = cotización del último día hábil ANTES
    del día. Valores esperados = los del Excel oficial de SUNAT (tcS01Alias)."""
    cotizaciones = parse_respuesta(_BCRP_ENE_2019)
    forma_sunat = normalizar_a_forma_sunat(
        cotizaciones, date(2019, 1, 3), date(2019, 1, 8)
    )

    # (compra, venta) que SUNAT publica por día — verificado contra el Excel.
    esperado = {
        date(2019, 1, 3): (Decimal("3.369"), Decimal("3.373")),
        date(2019, 1, 4): (Decimal("3.368"), Decimal("3.371")),
        date(2019, 1, 5): (Decimal("3.356"), Decimal("3.36")),  # sábado: arrastra vie 04
        date(2019, 1, 6): (Decimal("3.356"), Decimal("3.36")),  # domingo: arrastra vie 04
        date(2019, 1, 7): (Decimal("3.356"), Decimal("3.36")),  # lunes: aún el de vie 04
        date(2019, 1, 8): (Decimal("3.349"), Decimal("3.353")),  # martes: el de lun 07
    }
    obtenido = {f: (c.compra, c.venta) for f, c in forma_sunat.items()}
    assert obtenido == esperado


def test_normalizador_descarta_dias_sin_cotizacion_previa():
    """Un día anterior a la primera cotización no tiene vigente → queda fuera."""
    cotizaciones = [
        CotizacionSbs(fecha=date(2019, 1, 4), compra=Decimal("3.356"), venta=Decimal("3.36"))
    ]
    forma = normalizar_a_forma_sunat(cotizaciones, date(2019, 1, 3), date(2019, 1, 6))
    # 03 y 04 no tienen cotización ANTERIOR (la única es del 04, no < 04 ni < 03).
    assert set(forma.keys()) == {date(2019, 1, 5), date(2019, 1, 6)}


def test_upsert_tipos_cambio_es_idempotente(db_session):
    filas = {
        date(2026, 1, 2): CotizacionSbs(date(2026, 1, 2), Decimal("3.75"), Decimal("3.76")),
        date(2026, 1, 3): CotizacionSbs(date(2026, 1, 3), Decimal("3.75"), Decimal("3.77")),
    }
    assert repositorio.upsert_tipos_cambio(db_session, filas) == 2
    # Reaplicar con un valor cambiado: actualiza, no duplica.
    filas[date(2026, 1, 3)] = CotizacionSbs(date(2026, 1, 3), Decimal("3.80"), Decimal("3.81"))
    repositorio.upsert_tipos_cambio(db_session, filas)

    total = db_session.scalar(select(func.count()).select_from(TipoCambioModel))
    assert total == 2
    fila = db_session.get(TipoCambioModel, date(2026, 1, 3))
    assert fila.venta == Decimal("3.810")


def test_editar_manual_no_lo_pisa_el_refresco_automatico(db_session):
    """Un día con override manual sobrevive a un upsert automático del BCRP."""
    fecha = date(2026, 9, 7)
    repositorio.editar_manual(db_session, fecha, Decimal("3.400"), Decimal("3.410"))

    # El BCRP intenta sobrescribir el mismo día con otro valor.
    repositorio.upsert_tipos_cambio(
        db_session,
        {fecha: CotizacionSbs(fecha, Decimal("3.368"), Decimal("3.369"))},
        fuente="bcrp",
    )

    fila = db_session.get(TipoCambioModel, fecha)
    assert fila.fuente == "manual"
    assert fila.venta == Decimal("3.410")  # se conservó el manual


def test_editar_manual_crea_y_luego_actualiza(db_session):
    fecha = date(2026, 1, 15)
    creada = repositorio.editar_manual(db_session, fecha, Decimal("3.70"), Decimal("3.71"))
    assert creada.fuente == "manual" and creada.venta == Decimal("3.710")
    actualizada = repositorio.editar_manual(db_session, fecha, Decimal("3.80"), Decimal("3.81"))
    assert actualizada.venta == Decimal("3.810")


def test_listar_rango_devuelve_ordenado(db_session):
    repositorio.upsert_tipos_cambio(db_session, {
        date(2026, 1, 3): CotizacionSbs(date(2026, 1, 3), Decimal("3.75"), Decimal("3.77")),
        date(2026, 1, 2): CotizacionSbs(date(2026, 1, 2), Decimal("3.75"), Decimal("3.76")),
    })
    filas = repositorio.listar_rango(db_session, date(2026, 1, 1), date(2026, 1, 31))
    assert [f.fecha for f in filas] == [date(2026, 1, 2), date(2026, 1, 3)]


def test_asegurar_cobertura_baja_solo_el_rango_faltante(db_session, monkeypatch):
    """Si ya hay TC para parte de las fechas, solo se baja del BCRP lo que falta."""
    # Sembramos el 2 y 3; pediremos cobertura para 2, 3 y 6 → solo falta el 6.
    repositorio.upsert_tipos_cambio(db_session, {
        date(2026, 1, 2): CotizacionSbs(date(2026, 1, 2), Decimal("3.75"), Decimal("3.76")),
        date(2026, 1, 3): CotizacionSbs(date(2026, 1, 3), Decimal("3.75"), Decimal("3.77")),
    })

    llamadas: list[tuple[date, date]] = []

    def fake_obtener(desde: date, hasta: date) -> list[CotizacionSbs]:
        llamadas.append((desde, hasta))
        return [CotizacionSbs(date(2026, 1, 5), Decimal("3.78"), Decimal("3.79"))]

    monkeypatch.setattr(bcrp, "obtener_cotizaciones", fake_obtener)

    escritas = sincronizador.asegurar_cobertura(
        db_session, {date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 6)}
    )

    # Solo faltaba el 6 → el rango pedido arranca y termina en el 6 (con lead-in atrás).
    assert len(llamadas) == 1
    assert llamadas[0][1] == date(2026, 1, 6)
    assert escritas >= 1
    assert db_session.get(TipoCambioModel, date(2026, 1, 6)) is not None
