"""Entitlements de módulos. ¿La empresa tiene contratado el módulo dado?"""
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.platform.authorization.models import CompanyModule, CompanyModuleStatus


def company_has_module(db: Session, company_id: int, module_key: str) -> bool:
    entitlement = db.scalar(
        select(CompanyModule).where(
            CompanyModule.company_id == company_id,
            CompanyModule.module_key == module_key,
            CompanyModule.status == CompanyModuleStatus.activo,
        )
    )
    if entitlement is None:
        return False
    if entitlement.fecha_fin is not None and entitlement.fecha_fin < date.today():
        return False
    return True


def active_module_keys(db: Session, company_ids: list[int]) -> dict[int, list[str]]:
    """
    Módulos activos (no suspendidos ni vencidos) por empresa, en una sola
    consulta. Lo usa `/me` para que el frontend muestre solo los módulos que la
    empresa tiene contratados (misma regla que `company_has_module`).
    """
    if not company_ids:
        return {}
    hoy = date.today()
    rows = db.execute(
        select(CompanyModule.company_id, CompanyModule.module_key).where(
            CompanyModule.company_id.in_(company_ids),
            CompanyModule.status == CompanyModuleStatus.activo,
            or_(CompanyModule.fecha_fin.is_(None), CompanyModule.fecha_fin >= hoy),
        )
    ).all()
    modulos_por_empresa: dict[int, list[str]] = {cid: [] for cid in company_ids}
    for company_id, module_key in rows:
        modulos_por_empresa[company_id].append(module_key)
    return modulos_por_empresa
