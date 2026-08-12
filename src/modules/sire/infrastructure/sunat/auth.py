import httpx
from datetime import datetime, timedelta, timezone
from src.platform.security import decrypt_field
from src.modules.sire.infrastructure.models import SireCredentialsModel as CompanyCredentials

_token_cache: dict[int, dict] = {}

SUNAT_TOKEN_URL = "https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/"
SCOPE = "https://api-sire.sunat.gob.pe"


async def get_sunat_token(
    company_id: int,
    creds: CompanyCredentials,
    ruc: str,
    force_refresh: bool = False,
) -> str:
    """
    Devuelve un token OAuth 2.0 válido para la empresa.
    Usa caché en memoria para no re-autenticar en cada llamada.
    force_refresh: ignora el caché — usar cuando SUNAT devolvió 401
    (SUNAT invalida el token anterior al emitir uno nuevo para el mismo usuario SOL).
    """
    cached = _token_cache.get(company_id)
    if not force_refresh and cached and cached["expires_at"] > datetime.now(timezone.utc):
        return cached["token"]

    client_id = creds.client_id
    client_secret = decrypt_field(creds.client_secret_enc)
    clave_sol = decrypt_field(creds.clave_sol_enc)
    usuario_sol = creds.usuario_sol

    url = SUNAT_TOKEN_URL.format(client_id=client_id)
    data = {
        "grant_type": "password",
        "scope": SCOPE,
        "client_id": client_id,
        "client_secret": client_secret,
        "username": f"{ruc}{usuario_sol}",
        "password": clave_sol,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, data=data)

    if response.status_code != 200:
        raise ValueError(f"Error al autenticar con SUNAT: {response.status_code} — {response.text}")

    token_data = response.json()
    access_token = token_data["access_token"]
    expires_in = token_data.get("expires_in", 3600)

    _token_cache[company_id] = {
        "token": access_token,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60),
    }

    return access_token


def invalidate_token(company_id: int) -> None:
    _token_cache.pop(company_id, None)
