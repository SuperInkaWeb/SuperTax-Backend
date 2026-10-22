"""
Casos de uso de credenciales SOL (para el login web de SUNAT).

La fuente única es la bóveda de plataforma (`platform.credentials.vault`, clase
`sol`), compartida con SIRE. Se mantiene un *fallback* de lectura al almacén
legacy del módulo para las empresas cuya credencial aún no se ha re-guardado tras
la consolidación. El RUC es de la empresa (`core.companies`), no de la credencial.
"""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.modules.sunat.infrastructure.repositories import SqlSunatCredentialsRepository
from src.platform.credentials import vault
from src.platform.security import decrypt_field
from src.platform.tenancy.models import Company


@dataclass
class CredentialsStatus:
    configured: bool
    ruc: str | None = None
    usuario: str | None = None


def set_credentials(
    db: Session,
    company_id: int,
    user_id: int,
    ruc: str,  # noqa: ARG001 — el RUC lo fija la empresa; se acepta por compatibilidad del form
    usuario: str,
    clave: str,
) -> None:
    """Clave vacía = conservar la ya guardada (actualización parcial)."""
    vault.write(
        db, company_id, vault.KIND_SOL, identifier=usuario, secret=clave, user_id=user_id
    )


def get_credentials_status(db: Session, company_id: int) -> CredentialsStatus:
    sol = vault.get(db, company_id, vault.KIND_SOL)
    if sol is None:
        return _estado_legacy(db, company_id)
    company = db.get(Company, company_id)
    return CredentialsStatus(
        configured=True, ruc=company.ruc if company else None, usuario=sol.identifier
    )


def get_saved_login(db: Session, company_id: int) -> tuple[str, str, str] | None:
    """(ruc, usuario, clave) descifrados para usar en un job; None si no hay guardadas."""
    company = db.get(Company, company_id)
    if company is None:
        return None
    sol = vault.get(db, company_id, vault.KIND_SOL)
    if sol is not None:
        return company.ruc, sol.identifier, sol.secret
    return _login_legacy(db, company_id)


def _estado_legacy(db: Session, company_id: int) -> CredentialsStatus:
    creds = SqlSunatCredentialsRepository(db).get(company_id)
    if creds is None:
        return CredentialsStatus(configured=False)
    # El usuario SOL no es secreto crítico → se expone para prellenar el formulario.
    return CredentialsStatus(
        configured=True, ruc=creds.ruc, usuario=decrypt_field(creds.usuario_enc)
    )


def _login_legacy(db: Session, company_id: int) -> tuple[str, str, str] | None:
    creds = SqlSunatCredentialsRepository(db).get(company_id)
    if creds is None:
        return None
    return creds.ruc, decrypt_field(creds.usuario_enc), decrypt_field(creds.clave_enc)
