"""
Casos de uso de credenciales SOL (para el login web de SUNAT).

Usuario y clave SOL se cifran antes de persistir y nunca se devuelven; la consulta
solo expone si están configuradas y el RUC.
"""
from dataclasses import dataclass

from src.modules.sunat.infrastructure.repositories import SqlSunatCredentialsRepository
from src.platform.security import decrypt_field, encrypt_field


@dataclass
class CredentialsStatus:
    configured: bool
    ruc: str | None = None
    usuario: str | None = None


def set_credentials(
    repo: SqlSunatCredentialsRepository,
    company_id: int,
    user_id: int,
    ruc: str,
    usuario: str,
    clave: str,
) -> None:
    """Clave vacía = conservar la ya guardada (actualización parcial)."""
    existing = repo.get(company_id)
    if clave:
        clave_enc = encrypt_field(clave)
    elif existing is not None:
        clave_enc = existing.clave_enc
    else:
        raise ValueError("La clave SOL es obligatoria")
    repo.upsert(
        company_id=company_id,
        updated_by_id=user_id,
        ruc=ruc,
        usuario_enc=encrypt_field(usuario),
        clave_enc=clave_enc,
    )


def get_credentials_status(
    repo: SqlSunatCredentialsRepository, company_id: int
) -> CredentialsStatus:
    creds = repo.get(company_id)
    if creds is None:
        return CredentialsStatus(configured=False)
    # El usuario SOL no es secreto crítico → se expone para prellenar el formulario.
    return CredentialsStatus(
        configured=True, ruc=creds.ruc, usuario=decrypt_field(creds.usuario_enc)
    )


def get_saved_login(
    repo: SqlSunatCredentialsRepository, company_id: int
) -> tuple[str, str, str] | None:
    """(ruc, usuario, clave) descifrados para usar en un job; None si no hay guardadas."""
    creds = repo.get(company_id)
    if creds is None:
        return None
    return creds.ruc, decrypt_field(creds.usuario_enc), decrypt_field(creds.clave_enc)
