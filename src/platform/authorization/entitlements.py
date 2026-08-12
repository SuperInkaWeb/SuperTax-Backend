"""Entitlements de módulos. ¿La empresa tiene contratado el módulo dado?"""
from datetime import date

from sqlalchemy import select
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
