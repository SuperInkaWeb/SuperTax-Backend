"""
Access token válido de una conexión de buzón: descifra el guardado y, si está por
vencer, lo refresca con el refresh token y persiste el nuevo (también cifrado).
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from src.modules.correos.application import mailbox_oauth
from src.modules.correos.infrastructure.models import MailboxConnectionModel, MailboxStatus
from src.platform.database.base import utcnow
from src.platform.security import decrypt_field, encrypt_field

# Margen para refrescar antes del vencimiento real (evita usar un token que caduca
# justo durante un escaneo largo).
_MARGEN = timedelta(minutes=5)


def access_token_valido(db: Session, conn: MailboxConnectionModel) -> str:
    """Devuelve un access token usable (refrescándolo si hace falta). Lanza
    mailbox_oauth.OAuthError si la conexión caducó y no se puede refrescar."""
    vigente = conn.expires_at is not None and conn.expires_at - _MARGEN > utcnow()
    if vigente:
        return decrypt_field(conn.access_token)

    if not conn.refresh_token:
        _marcar_error(db, conn)
        raise mailbox_oauth.OAuthError("El acceso al buzón caducó. Vuelve a conectarlo.", 401)

    try:
        tokens = mailbox_oauth.refrescar(conn.provider, decrypt_field(conn.refresh_token))
    except mailbox_oauth.OAuthError:
        _marcar_error(db, conn)
        raise

    access = str(tokens["access_token"])
    conn.access_token = encrypt_field(access)
    if tokens.get("refresh_token"):  # algunos proveedores rotan el refresh token
        conn.refresh_token = encrypt_field(str(tokens["refresh_token"]))
    conn.expires_at = utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    conn.status = MailboxStatus.active.value
    db.commit()
    return access


def _marcar_error(db: Session, conn: MailboxConnectionModel) -> None:
    conn.status = MailboxStatus.error.value
    db.commit()
