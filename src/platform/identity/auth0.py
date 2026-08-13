"""
Validación de access tokens de Auth0 (RS256 vía JWKS, con caché).

Auth0 es el único proveedor de identidad de la plataforma. Se valida firma,
audience e issuer contra el JWKS del tenant. El JWKS se cachea 1 hora.
"""
import time

import httpx
from jose import JWTError, jwt

from src.platform.config.settings import settings

_jwks_cache: dict = {"keys": None, "expira": 0.0}


class Auth0Error(Exception):
    pass


def _issuer() -> str:
    return f"https://{settings.AUTH0_DOMAIN}/"


def _obtener_jwks() -> dict:
    now = time.monotonic()
    if _jwks_cache["keys"] and now < _jwks_cache["expira"]:
        return _jwks_cache["keys"]
    resp = httpx.get(f"{_issuer()}.well-known/jwks.json", timeout=10)
    resp.raise_for_status()
    _jwks_cache["keys"] = resp.json()
    _jwks_cache["expira"] = now + 3600
    return _jwks_cache["keys"]


def validar_token(token: str) -> dict:
    """Valida un access token de Auth0 y devuelve sus claims, o lanza Auth0Error."""
    if not settings.AUTH0_DOMAIN or not settings.AUTH0_AUDIENCE:
        raise Auth0Error("Auth0 no está configurado (AUTH0_DOMAIN / AUTH0_AUDIENCE)")

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise Auth0Error(f"Token malformado: {e}")

    jwks = _obtener_jwks()
    key = _buscar_clave(jwks, header.get("kid"))
    if key is None:
        # La clave pudo rotar: invalida caché y reintenta una vez.
        _jwks_cache["expira"] = 0.0
        key = _buscar_clave(_obtener_jwks(), header.get("kid"))
        if key is None:
            raise Auth0Error("Clave de firma desconocida")

    try:
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.AUTH0_AUDIENCE,
            issuer=_issuer(),
        )
    except JWTError as e:
        raise Auth0Error(f"Token inválido: {e}")


def _buscar_clave(jwks: dict, kid: str | None) -> dict | None:
    return next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)


# ───────────────────────── Management API ─────────────────────────
# Crear/gestionar usuarios desde el backend (onboarding e invitaciones). Auth0
# gestiona la contraseña: el usuario recibe un email para establecerla.
_mgmt_token_cache: dict = {"token": None, "expira": 0.0}


def _mgmt_token() -> str:
    now = time.monotonic()
    if _mgmt_token_cache["token"] and now < _mgmt_token_cache["expira"]:
        return _mgmt_token_cache["token"]
    resp = httpx.post(
        f"{_issuer()}oauth/token",
        json={
            "grant_type": "client_credentials",
            "client_id": settings.AUTH0_MGMT_CLIENT_ID,
            "client_secret": settings.AUTH0_MGMT_CLIENT_SECRET,
            "audience": f"{_issuer()}api/v2/",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise Auth0Error(f"No se pudo obtener token del Management API: {resp.text[:200]}")
    data = resp.json()
    _mgmt_token_cache["token"] = data["access_token"]
    _mgmt_token_cache["expira"] = now + data.get("expires_in", 3600) - 60
    return _mgmt_token_cache["token"]


def crear_usuario(email: str, nombre: str, password: str) -> str:
    """Crea el usuario en Auth0 y devuelve su ID (sub)."""
    resp = httpx.post(
        f"{_issuer()}api/v2/users",
        headers={"Authorization": f"Bearer {_mgmt_token()}"},
        json={
            "email": email,
            "name": nombre,
            "password": password,
            "connection": settings.AUTH0_DB_CONNECTION,
            "email_verified": False,
        },
        timeout=15,
    )
    if resp.status_code == 409:
        raise Auth0Error(f"El email {email} ya existe en Auth0")
    if resp.status_code != 201:
        raise Auth0Error(f"No se pudo crear el usuario en Auth0: {resp.text[:300]}")
    return resp.json()["user_id"]


def buscar_sub_por_email(email: str) -> str | None:
    """Busca un usuario existente en Auth0 y devuelve su ID (sub), o None."""
    resp = httpx.get(
        f"{_issuer()}api/v2/users-by-email",
        headers={"Authorization": f"Bearer {_mgmt_token()}"},
        params={"email": email},
        timeout=15,
    )
    if resp.status_code != 200:
        raise Auth0Error(f"No se pudo buscar el usuario en Auth0: {resp.text[:200]}")
    usuarios = resp.json()
    for u in usuarios:
        if u.get("user_id", "").startswith("auth0|"):
            return u["user_id"]
    return usuarios[0]["user_id"] if usuarios else None


def eliminar_usuario(auth0_sub: str) -> None:
    resp = httpx.delete(
        f"{_issuer()}api/v2/users/{auth0_sub}",
        headers={"Authorization": f"Bearer {_mgmt_token()}"},
        timeout=15,
    )
    if resp.status_code not in (204, 404):
        raise Auth0Error(f"No se pudo eliminar el usuario en Auth0: {resp.text[:200]}")


def enviar_reset_password(email: str) -> None:
    """Auth0 envía el email de 'establecer/cambiar contraseña' al usuario."""
    resp = httpx.post(
        f"{_issuer()}dbconnections/change_password",
        json={
            "client_id": settings.AUTH0_SPA_CLIENT_ID,
            "email": email,
            "connection": settings.AUTH0_DB_CONNECTION,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise Auth0Error(f"No se pudo enviar el email de contraseña: {resp.text[:200]}")
