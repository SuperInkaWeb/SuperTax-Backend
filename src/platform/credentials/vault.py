"""
Bóveda de credenciales de plataforma: fuente única por empresa.

Centraliza las credenciales que varios módulos comparten para eliminar la
duplicación. Hoy la credencial SOL (usuario + clave) se usa tanto en el login web
de SUNAT como en la autenticación de la API de SIRE; antes se guardaba por
separado en cada módulo. Vive en `platform` porque la comparten los módulos y el
núcleo no puede depender de ellos (los módulos sí dependen del núcleo).

Clases de credencial (`kind`):
  - KIND_SOL      → usuario + clave SOL (login SUNAT y autenticación API SIRE).
  - KIND_SIRE_API → client_id + client_secret del cliente OAuth de SUNAT (solo SIRE).

El `identifier` (usuario SOL / client_id) no es secreto y se guarda en claro para
prellenar formularios; el `secret` (clave SOL / client_secret) se cifra con Fernet
y nunca se persiste en claro.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.credentials.models import CompanyCredentialModel
from src.platform.security import decrypt_field, encrypt_field

KIND_SOL = "sol"
KIND_SIRE_API = "sire_api"


@dataclass(frozen=True)
class StoredCredential:
    """Credencial leída de la bóveda. El secreto se descifra solo bajo demanda."""

    identifier: str
    secret_enc: str
    updated_at: datetime

    @property
    def secret(self) -> str:
        """Secreto descifrado (clave SOL / client_secret)."""
        return decrypt_field(self.secret_enc)


def get(db: Session, company_id: int, kind: str) -> StoredCredential | None:
    row = db.scalar(
        select(CompanyCredentialModel).where(
            CompanyCredentialModel.company_id == company_id,
            CompanyCredentialModel.kind == kind,
        )
    )
    if row is None:
        return None
    return StoredCredential(row.identifier, row.secret_enc, row.updated_at)


def has_secret(db: Session, company_id: int, kind: str) -> bool:
    """True si ya hay un secreto guardado para esa clase (sin descifrarlo)."""
    return (
        db.scalar(
            select(CompanyCredentialModel.id).where(
                CompanyCredentialModel.company_id == company_id,
                CompanyCredentialModel.kind == kind,
            )
        )
        is not None
    )


def write(
    db: Session,
    company_id: int,
    kind: str,
    identifier: str,
    secret: str,
    user_id: int,
) -> None:
    """
    Upsert de una credencial.

    Secreto vacío = conservar el ya guardado (permite editar el identificador sin
    reescribir el secreto); si no hay ninguno previo, es un error de validación.
    """
    row = db.scalar(
        select(CompanyCredentialModel).where(
            CompanyCredentialModel.company_id == company_id,
            CompanyCredentialModel.kind == kind,
        )
    )
    if secret:
        secret_enc = encrypt_field(secret)
    elif row is not None:
        secret_enc = row.secret_enc
    else:
        raise ValueError("El secreto es obligatorio la primera vez")

    if row is None:
        row = CompanyCredentialModel(company_id=company_id, kind=kind)
        db.add(row)
    row.identifier = identifier
    row.secret_enc = secret_enc
    row.updated_by_id = user_id
    db.commit()
