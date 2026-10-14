"""
Vinculación de buzón por OAuth 2.0 (Authorization Code + PKCE), adaptado a la
plataforma (sync, sin Redis). Dos mitades:

  autorizar()  -> construye la URL de consentimiento. El `state` es un token CIFRADO
                  (Fernet) que lleva empresa, usuario, proveedor, el `code_verifier`
                  de PKCE y un vencimiento. Así no hace falta un store (Redis/tabla):
                  es anti-CSRF (solo el backend pudo emitirlo), vincula la empresa
                  activa al volver (el callback de Google/MS no trae token Auth0) y el
                  `code_verifier` nunca se expone (va cifrado).
  completar()  -> descifra y valida el `state`, canjea el código por tokens, resuelve
                  el correo de la cuenta y persiste la conexión con los tokens CIFRADOS.

Mismo patrón que el Drive OAuth de SUNAT, extendido a Gmail y Outlook con PKCE.
"""
import base64
import hashlib
import json
import secrets
import time
import urllib.parse
from dataclasses import dataclass

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from src.modules.correos.infrastructure.models import MailboxConnectionModel, MailboxProvider
from src.modules.correos.infrastructure.repositories import SqlMailboxRepository
from src.platform.config.settings import settings
from src.platform.security import decrypt_field, encrypt_field

_TIMEOUT = 15.0


class OAuthError(Exception):
    """Fallo en el flujo de vinculación (mensaje apto para el usuario)."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class _ProviderCfg:
    auth_endpoint: str
    token_endpoint: str
    scope: str
    extra_auth: dict[str, str]
    client_id: str
    client_secret: str
    # Subcadena que el scope concedido debe contener (lectura de correo).
    scope_requerido: str


def _providers() -> dict[str, _ProviderCfg]:
    return {
        MailboxProvider.gmail.value: _ProviderCfg(
            auth_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            scope="openid email https://www.googleapis.com/auth/gmail.readonly",
            extra_auth={"access_type": "offline", "prompt": "consent",
                        "include_granted_scopes": "false"},
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            scope_requerido="gmail.readonly",
        ),
        MailboxProvider.outlook.value: _ProviderCfg(
            auth_endpoint=f"https://login.microsoftonline.com/{settings.MS_TENANT}/oauth2/v2.0/authorize",
            token_endpoint=f"https://login.microsoftonline.com/{settings.MS_TENANT}/oauth2/v2.0/token",
            scope="openid email offline_access https://graph.microsoft.com/Mail.Read",
            extra_auth={"response_mode": "query", "prompt": "select_account"},
            client_id=settings.MS_CLIENT_ID,
            client_secret=settings.MS_CLIENT_SECRET,
            scope_requerido="mail.read",
        ),
    }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _cfg(provider: str) -> _ProviderCfg:
    cfg = _providers().get(provider)
    if cfg is None:
        raise OAuthError("Proveedor no soportado.")
    if not cfg.client_id or not cfg.client_secret:
        raise OAuthError("El proveedor no está configurado en el servidor.", status_code=503)
    return cfg


def autorizar(company_id: int, user_id: int, provider: str) -> str:
    """Devuelve la URL de consentimiento (con el `state` cifrado y el PKCE challenge)."""
    cfg = _cfg(provider)
    verifier, challenge = _pkce()
    state = encrypt_field(json.dumps({
        "c": company_id, "u": user_id, "p": provider, "v": verifier,
        "exp": int(time.time()) + settings.CORREOS_OAUTH_STATE_TTL_SECONDS,
    }))
    params = {
        "client_id": cfg.client_id,
        "redirect_uri": settings.CORREOS_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": cfg.scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **cfg.extra_auth,
    }
    return cfg.auth_endpoint + "?" + urllib.parse.urlencode(params)


def _leer_state(state: str, company_id: int, user_id: int) -> dict:
    try:
        data = json.loads(decrypt_field(state))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise OAuthError("La solicitud de vinculación no es válida o expiró.") from exc
    if data.get("exp", 0) < int(time.time()):
        raise OAuthError("La solicitud de vinculación expiró. Vuelve a intentarlo.")
    # El que completa debe ser el mismo que inició (anti-CSRF/login-CSRF).
    if int(data.get("c", -1)) != company_id or int(data.get("u", -1)) != user_id:
        raise OAuthError("La solicitud de vinculación no corresponde a esta sesión.", 403)
    return data


def _canjear_codigo(cfg: _ProviderCfg, code: str, verifier: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.CORREOS_OAUTH_REDIRECT_URI,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "code_verifier": verifier,
    }
    try:
        resp = httpx.post(cfg.token_endpoint, data=data,
                          headers={"Accept": "application/json"}, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise OAuthError("No se pudo conectar con el proveedor de correo.", 502) from exc
    if resp.status_code != httpx.codes.OK:
        raise OAuthError("El proveedor rechazó la vinculación. Vuelve a intentarlo.")
    es_json = resp.headers.get("content-type", "").startswith("application/json")
    cuerpo = resp.json() if es_json else {}
    if not cuerpo.get("access_token"):
        raise OAuthError("El proveedor no devolvió un token válido.")
    return cuerpo


def _correo_de_la_cuenta(cfg: _ProviderCfg, tokens: dict) -> str:
    """Correo del buzón para mostrar en la UI (informativo, no de seguridad)."""
    if cfg.scope_requerido == "mail.read":  # Outlook: del id_token, sin llamar a Graph
        return _correo_de_id_token(tokens.get("id_token")) or ""
    try:  # Gmail: userinfo
        resp = httpx.get("https://www.googleapis.com/oauth2/v3/userinfo",
                         headers={"Authorization": f"Bearer {tokens['access_token']}"},
                         timeout=_TIMEOUT)
        if resp.status_code == httpx.codes.OK:
            return str(resp.json().get("email") or "")
    except httpx.HTTPError:
        pass
    return ""


def _correo_de_id_token(id_token: str | None) -> str | None:
    if not id_token:
        return None
    try:
        payload_b64 = id_token.split(".")[1]
        relleno = "=" * (-len(payload_b64) % 4)
        datos = json.loads(base64.urlsafe_b64decode(payload_b64 + relleno))
    except (ValueError, IndexError):
        return None
    correo = datos.get("email") or datos.get("preferred_username")
    return str(correo) if correo else None


def completar(
    db: Session, company_id: int, user_id: int, code: str, state: str
) -> MailboxConnectionModel:
    """Valida el state, canjea el código y guarda la conexión con tokens cifrados."""
    data = _leer_state(state, company_id, user_id)
    provider = str(data["p"])
    cfg = _cfg(provider)
    tokens = _canjear_codigo(cfg, code, str(data["v"]))

    scopes = (tokens.get("scope") or "").split()
    if not any(cfg.scope_requerido in s.lower() for s in scopes):
        raise OAuthError(
            "No se concedió el permiso de lectura de correo. "
            "Vuelve a conectar aceptando todos los permisos solicitados."
        )

    refresh = tokens.get("refresh_token")
    return SqlMailboxRepository(db).upsert_connection(
        company_id=company_id,
        created_by_id=user_id,
        provider=provider,
        account_email=_correo_de_la_cuenta(cfg, tokens),
        access_token_enc=encrypt_field(str(tokens["access_token"])),
        refresh_token_enc=encrypt_field(refresh) if refresh else None,
        scopes=scopes,
        expires_in_seconds=int(tokens.get("expires_in", 3600)),
    )
