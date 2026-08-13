"""
Endpoints de administración de empresas y de sus módulos contratados.

Todo aquí es acción de plataforma (SuperAdmin): crear/editar empresas y habilitar
o suspender los módulos que cada empresa tiene contratados (entitlements).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.deps import require_platform_admin
from src.platform.authorization.models import (
    CompanyModule,
    CompanyModuleStatus,
    Module,
)
from src.platform.database.session import get_db
from src.platform.tenancy.models import Company, CompanyStatus
from src.platform.users.models import User

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyInput(BaseModel):
    ruc: str = Field(min_length=11, max_length=11)
    razon_social: str = Field(min_length=1, max_length=200)


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ruc: str
    razon_social: str
    status: CompanyStatus
    created_at: datetime


class ModuleEntitlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    module_key: str
    status: CompanyModuleStatus


class ModuleEntitlementInput(BaseModel):
    module_key: str


@router.get("", response_model=list[CompanyResponse])
def list_companies(
    _admin: User = Depends(require_platform_admin), db: Session = Depends(get_db)
) -> list[CompanyResponse]:
    rows = db.scalars(select(Company).order_by(Company.razon_social)).all()
    return [CompanyResponse.model_validate(c) for c in rows]


@router.post("", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
def create_company(
    payload: CompanyInput,
    _admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> CompanyResponse:
    if db.scalar(select(Company).where(Company.ruc == payload.ruc)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="El RUC ya está registrado")
    company = Company(ruc=payload.ruc, razon_social=payload.razon_social)
    db.add(company)
    db.commit()
    db.refresh(company)
    return CompanyResponse.model_validate(company)


@router.put("/{company_id}", response_model=CompanyResponse)
def update_company(
    company_id: int,
    payload: CompanyInput,
    _admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> CompanyResponse:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Empresa no encontrada")
    company.ruc = payload.ruc
    company.razon_social = payload.razon_social
    db.commit()
    db.refresh(company)
    return CompanyResponse.model_validate(company)


@router.get("/{company_id}/modules", response_model=list[ModuleEntitlementResponse])
def list_company_modules(
    company_id: int,
    _admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> list[ModuleEntitlementResponse]:
    rows = db.scalars(
        select(CompanyModule).where(CompanyModule.company_id == company_id)
    ).all()
    return [ModuleEntitlementResponse.model_validate(m) for m in rows]


@router.post(
    "/{company_id}/modules",
    response_model=ModuleEntitlementResponse,
    status_code=status.HTTP_201_CREATED,
)
def enable_company_module(
    company_id: int,
    payload: ModuleEntitlementInput,
    _admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> ModuleEntitlementResponse:
    if db.get(Company, company_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Empresa no encontrada")
    if db.scalar(select(Module).where(Module.key == payload.module_key)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Módulo desconocido")

    entitlement = db.scalar(
        select(CompanyModule).where(
            CompanyModule.company_id == company_id,
            CompanyModule.module_key == payload.module_key,
        )
    )
    if entitlement is None:
        entitlement = CompanyModule(company_id=company_id, module_key=payload.module_key)
        db.add(entitlement)
    entitlement.status = CompanyModuleStatus.activo
    db.commit()
    db.refresh(entitlement)
    return ModuleEntitlementResponse.model_validate(entitlement)


@router.delete("/{company_id}/modules/{module_key}", status_code=status.HTTP_204_NO_CONTENT)
def disable_company_module(
    company_id: int,
    module_key: str,
    _admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> None:
    entitlement = db.scalar(
        select(CompanyModule).where(
            CompanyModule.company_id == company_id,
            CompanyModule.module_key == module_key,
        )
    )
    if entitlement is not None:
        entitlement.status = CompanyModuleStatus.suspendido
        db.commit()
