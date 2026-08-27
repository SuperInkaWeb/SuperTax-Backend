"""
Conexión con Google Drive (OAuth 2.0) por empresa.

El `state` del flujo OAuth es un token cifrado (Fernet) con la empresa y el
usuario que iniciaron la conexión: sirve de protección CSRF (solo el backend
pudo emitirlo) y de vínculo con la empresa activa al volver del callback de
Google, que no lleva token Auth0.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from src.modules.sunat.infrastructure.repositories import SqlDriveTokenRepository
from src.platform.config.settings import settings
from src.platform.security import decrypt_field, encrypt_field

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# Scope acotado: la app solo gestiona sus propios archivos (la carpeta que crea
# para subir los comprobantes). Evita la verificación de Google del scope amplio
# 'drive'. El Excel de entrada ya no se lee del Drive: se elige con el Picker.
_SCOPE = "https://www.googleapis.com/auth/drive.file"


class DriveError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _firmar_state(company_id: int, user_id: int) -> str:
    return encrypt_field(json.dumps({"c": company_id, "u": user_id}))


def _leer_state(state: str) -> int:
    try:
        data = json.loads(decrypt_field(state))
    except (InvalidToken, ValueError, TypeError):
        raise DriveError("Estado inválido")
    return int(data["c"])


def url_autorizacion(company_id: int, user_id: int) -> str:
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.DRIVE_REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": _firmar_state(company_id, user_id),
    }
    return _AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _intercambiar_code(code: str) -> dict:
    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.DRIVE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request(
        _TOKEN_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310 — endpoint fijo de Google
        return json.loads(resp.read())


def procesar_callback(db: Session, code: str, state: str) -> None:
    """Valida el state, intercambia el code y guarda los tokens cifrados."""
    company_id = _leer_state(state)
    try:
        token_data = _intercambiar_code(code)
    except urllib.error.HTTPError:
        raise DriveError("Error al conectar con Google Drive")

    SqlDriveTokenRepository(db).upsert(
        company_id,
        access_enc=encrypt_field(token_data.get("access_token", "")),
        refresh_enc=encrypt_field(token_data.get("refresh_token", "")),
    )


def desconectar(db: Session, company_id: int) -> None:
    SqlDriveTokenRepository(db).delete(company_id)
