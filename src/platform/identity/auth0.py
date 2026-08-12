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
