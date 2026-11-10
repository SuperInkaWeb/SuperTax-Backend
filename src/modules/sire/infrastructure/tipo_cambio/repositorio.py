"""Persistencia de la tabla global de tipo de cambio (upsert por fecha)."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.modules.sire.infrastructure.models import TipoCambioModel
from src.modules.sire.infrastructure.tipo_cambio.bcrp import CotizacionSbs
from src.platform.database.base import utcnow


def upsert_tipos_cambio(
    db: Session, cotizaciones: dict[date, CotizacionSbs], fuente: str = "bcrp"
) -> int:
    """Inserta o actualiza el TC por día calendario. Devuelve cuántas filas tocó."""
    if not cotizaciones:
        return 0
    ahora = utcnow()
    valores = [
        {
            "fecha": fecha,
            "compra": cot.compra,
            "venta": cot.venta,
            "fuente": fuente,
            "updated_at": ahora,
        }
        for fecha, cot in cotizaciones.items()
    ]
    stmt = pg_insert(TipoCambioModel).values(valores)
    stmt = stmt.on_conflict_do_update(
        index_elements=["fecha"],
        set_={
            "compra": stmt.excluded.compra,
            "venta": stmt.excluded.venta,
            "fuente": stmt.excluded.fuente,
            "updated_at": stmt.excluded.updated_at,
        },
        # Un día con override manual no se pisa con el valor automático del BCRP.
        where=TipoCambioModel.fuente != "manual",
    )
    db.execute(stmt)
    db.commit()
    return len(valores)


def listar_rango(db: Session, desde: date, hasta: date) -> list[TipoCambioModel]:
    """TC por día en [desde, hasta], ordenado por fecha (para la pantalla admin)."""
    return list(
        db.scalars(
            select(TipoCambioModel)
            .where(TipoCambioModel.fecha >= desde, TipoCambioModel.fecha <= hasta)
            .order_by(TipoCambioModel.fecha)
        ).all()
    )


def editar_manual(db: Session, fecha: date, compra, venta) -> TipoCambioModel:
    """Fija a mano el TC de un día (fuente='manual'); sobrescribe siempre. Es la
    corrección del admin cuando el BCRP falta o difiere."""
    fila = db.get(TipoCambioModel, fecha)
    if fila is None:
        fila = TipoCambioModel(fecha=fecha, compra=compra, venta=venta, fuente="manual")
        db.add(fila)
    else:
        fila.compra = compra
        fila.venta = venta
        fila.fuente = "manual"
    db.commit()
    db.refresh(fila)
    return fila


def fechas_existentes(db: Session, desde: date, hasta: date) -> set[date]:
    """Fechas ya presentes en la tabla dentro de [desde, hasta]."""
    return set(
        db.scalars(
            select(TipoCambioModel.fecha).where(
                TipoCambioModel.fecha >= desde, TipoCambioModel.fecha <= hasta
            )
        ).all()
    )


def obtener_por_fechas(db: Session, fechas: set[date]) -> dict[date, TipoCambioModel]:
    """TC indexado por fecha para el conjunto de fechas pedido."""
    if not fechas:
        return {}
    filas = db.scalars(
        select(TipoCambioModel).where(TipoCambioModel.fecha.in_(fechas))
    ).all()
    return {fila.fecha: fila for fila in filas}
