"""
Cliente de la Consulta Integrada de validez de comprobante de pago de SUNAT
(servicio `validarcomprobante`). Devuelve el estado del comprobante (ACEPTADO,
NO EXISTE, ANULADO…) para la columna «Rpta SUNAT» del reporte SIRE.

A diferencia del token SIRE (grant_type=password, endpoint clientessol, scope
api-sire), la validez usa OAuth `client_credentials` sobre `clientesextranet` con
scope de contribuyentes y SOLO el client_id/secret (no requiere la Clave SOL). Se
reusan las credenciales de API del SIRE; si SUNAT rechaza el scope, no hay validez
(se degrada en silencio, nunca rompe la conciliación).

Spec: Manual de Consulta Integrada de Comprobante de Pago por Servicio Web (SUNAT).
"""
import httpx

_TOKEN_URL = "https://api-seguridad.sunat.gob.pe/v1/clientesextranet/{client_id}/oauth2/token/"
_SCOPE = "https://api.sunat.gob.pe/v1/contribuyente/contribuyentes"
_VALIDAR_URL = (
    "https://api.sunat.gob.pe/v1/contribuyente/contribuyentes/{ruc}/validarcomprobante"
)

# estadoCp → etiqueta (Anexo del manual).
ESTADO_CP = {
    "0": "NO EXISTE",
    "1": "ACEPTADO",
    "2": "ANULADO",
    "3": "AUTORIZADO",
    "4": "NO AUTORIZADO",
}


class TokenExpirado(Exception):
    """El token de validez expiró (401): el llamador debe pedir uno nuevo y reintentar."""


def obtener_token(client_id: str, client_secret: str, timeout: int = 30) -> str | None:
    """Token OAuth client_credentials para el scope de validez. None si SUNAT lo
    rechaza (p. ej. las credenciales no están habilitadas para este servicio)."""
    url = _TOKEN_URL.format(client_id=client_id)
    data = {
        "grant_type": "client_credentials",
        "scope": _SCOPE,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        resp = httpx.post(url, data=data, timeout=timeout)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return resp.json().get("access_token")


def validar(
    token: str,
    ruc_consultante: str,
    num_ruc: str,
    cod_comp: str,
    serie: str,
    numero: str,
    fecha_emision: str,
    monto: float | None = None,
    timeout: int = 30,
) -> str | None:
    """Estado del comprobante (etiqueta de ESTADO_CP) o None si no se pudo consultar.

    `ruc_consultante` va en la URL (quien consulta); `num_ruc` es el emisor. Para
    comprobantes electrónicos se envía el monto total. `fecha_emision`: dd/mm/aaaa.
    """
    try:
        numero_int = str(int(numero))
    except (TypeError, ValueError):
        return None
    body = {
        "numRuc": num_ruc,
        "codComp": cod_comp,
        "numeroSerie": serie,
        "numero": numero_int,
        "fechaEmision": fecha_emision,
    }
    if monto is not None:
        body["monto"] = f"{monto:.2f}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = httpx.post(
            _VALIDAR_URL.format(ruc=ruc_consultante), json=body, headers=headers, timeout=timeout
        )
    except httpx.HTTPError:
        return None
    if resp.status_code == 401:
        raise TokenExpirado()
    if resp.status_code != 200:
        return None
    data = resp.json().get("data") or {}
    return ESTADO_CP.get(str(data.get("estadoCp", "")))
