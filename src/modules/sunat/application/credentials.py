"""
Casos de uso de credenciales SOL (para el login web de SUNAT).

Usuario y clave SOL se cifran antes de persistir y nunca se devuelven; la consulta
solo expone si están configuradas y el RUC.
"""
from dataclasses import dataclass

from src.modules.sunat.infrastructure.repositories import SqlSunatCredentialsRepository
from src.platform.security import encrypt_field


@dataclass
class CredentialsStatus:
    configured: bool
    ruc: str | None = None


def set_credentials(
    repo: SqlSunatCredentialsRepository,
    company_id: int,
    user_id: int,
    ruc: str,
    usuario: str,
    clave: str,
) -> None:
    repo.upsert(
        company_id=company_id,
        updated_by_id=user_id,
        ruc=ruc,
        usuario_enc=encrypt_field(usuario),
        clave_enc=encrypt_field(clave),
    )


def get_credentials_status(
    repo: SqlSunatCredentialsRepository, company_id: int
) -> CredentialsStatus:
    creds = repo.get(company_id)
    if creds is None:
        return CredentialsStatus(configured=False)
    return CredentialsStatus(configured=True, ruc=creds.ruc)
