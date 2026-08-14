"""
Casos de uso de credenciales SUNAT-SIRE de la empresa.

Los secretos (clave SOL, client_secret) se cifran antes de persistir y **nunca**
se devuelven: la consulta solo expone un estado (configurado o no) y los campos
no sensibles.
"""
from dataclasses import dataclass
from datetime import datetime

from src.modules.sire.infrastructure.repositories import SqlCredentialsRepository
from src.platform.security import encrypt_field


@dataclass
class CredentialsStatus:
    configured: bool
    usuario_sol: str | None = None
    client_id: str | None = None
    updated_at: datetime | None = None


def set_credentials(
    repo: SqlCredentialsRepository,
    company_id: int,
    user_id: int,
    usuario_sol: str,
    clave_sol: str,
    client_id: str,
    client_secret: str,
) -> None:
    """
    Actualiza credenciales. Los secretos vacíos se interpretan como "conservar el
    guardado" (permite editar solo usuario/client_id sin reescribir los secretos).
    """
    existing = repo.get(company_id)
    if clave_sol:
        clave_sol_enc = encrypt_field(clave_sol)
    elif existing is not None:
        clave_sol_enc = existing.clave_sol_enc
    else:
        raise ValueError("La clave SOL es obligatoria")

    if client_secret:
        client_secret_enc = encrypt_field(client_secret)
    elif existing is not None:
        client_secret_enc = existing.client_secret_enc
    else:
        raise ValueError("El client secret es obligatorio")

    repo.upsert(
        company_id=company_id,
        updated_by_id=user_id,
        usuario_sol=usuario_sol,
        clave_sol_enc=clave_sol_enc,
        client_id=client_id,
        client_secret_enc=client_secret_enc,
    )


def get_credentials_status(
    repo: SqlCredentialsRepository, company_id: int
) -> CredentialsStatus:
    creds = repo.get(company_id)
    if creds is None:
        return CredentialsStatus(configured=False)
    return CredentialsStatus(
        configured=True,
        usuario_sol=creds.usuario_sol,
        client_id=creds.client_id,
        updated_at=creds.updated_at,
    )
