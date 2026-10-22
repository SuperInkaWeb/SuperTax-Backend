"""
Casos de uso de credenciales SUNAT-SIRE de la empresa.

La fuente única es la bóveda de plataforma (`platform.credentials.vault`): la
credencial `sol` (usuario + clave SOL) se comparte con SUNAT, y `sire_api`
(client_id + client_secret del cliente OAuth) es propia de SIRE. Se mantiene un
*fallback* de lectura al almacén legacy del módulo mientras una empresa no haya
re-guardado sus credenciales tras la consolidación.

Los secretos (clave SOL, client_secret) se cifran antes de persistir y **nunca**
se devuelven: la consulta solo expone un estado y los campos no sensibles.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from src.modules.sire.infrastructure.repositories import SqlCredentialsRepository
from src.platform.credentials import vault


@dataclass
class CredentialsStatus:
    configured: bool
    usuario_sol: str | None = None
    client_id: str | None = None
    updated_at: datetime | None = None


def set_credentials(
    db: Session,
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

    Se validan ambos secretos antes de escribir: así una credencial nueva que
    llegue incompleta no deja la mitad guardada (comportamiento "todo o nada").
    """
    if not clave_sol and not vault.has_secret(db, company_id, vault.KIND_SOL):
        raise ValueError("La clave SOL es obligatoria")
    if not client_secret and not vault.has_secret(db, company_id, vault.KIND_SIRE_API):
        raise ValueError("El client secret es obligatorio")

    vault.write(
        db, company_id, vault.KIND_SOL,
        identifier=usuario_sol, secret=clave_sol, user_id=user_id,
    )
    vault.write(
        db, company_id, vault.KIND_SIRE_API,
        identifier=client_id, secret=client_secret, user_id=user_id,
    )


def get_credentials_status(db: Session, company_id: int) -> CredentialsStatus:
    sol = vault.get(db, company_id, vault.KIND_SOL)
    api = vault.get(db, company_id, vault.KIND_SIRE_API)
    if sol is None or api is None:
        return _estado_legacy(db, company_id)
    return CredentialsStatus(
        configured=True,
        usuario_sol=sol.identifier,
        client_id=api.identifier,
        updated_at=max(sol.updated_at, api.updated_at),
    )


def _estado_legacy(db: Session, company_id: int) -> CredentialsStatus:
    creds = SqlCredentialsRepository(db).get(company_id)
    if creds is None:
        return CredentialsStatus(configured=False)
    return CredentialsStatus(
        configured=True,
        usuario_sol=creds.usuario_sol,
        client_id=creds.client_id,
        updated_at=creds.updated_at,
    )
