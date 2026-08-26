"""
Cliente HTTP del servicio `consultacpe` de SUNAT: descarga el XML/PDF/CDR de un
comprobante recibido. Reemplaza la descarga por DOM (Playwright), que era frágil.

Endpoint (capturado del portal SOL):
  GET api-cpe.sunat.gob.pe/v1/contribuyente/consultacpe/comprobantes/
      {rucEmisor}-{tipo}-{serie}-{numero}-{origen}/{cod}
  cod: 01=PDF, 02=XML, 03=CDR.  origen: 2=recibido.
  Respuesta: JSON {nomArchivo, valArchivo} donde valArchivo = base64 de un ZIP.

Notas de robustez (probadas contra el portal real):
  - SUNAT tira 500 intermitentes en su propio endpoint -> se reintenta.
  - Sin cabecera User-Agent de navegador, el WAF corta la conexión sin responder.
"""
import base64
import io
import time
import zipfile

import httpx

_BASE = "https://api-cpe.sunat.gob.pe/v1/contribuyente/consultacpe/comprobantes"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_COD = {"pdf": "01", "xml": "02", "cdr": "03"}
_ORIGEN_RECIBIDO = "2"


class TokenExpirado(Exception):
    """El token de la sesión SUNAT expiró o dejó de ser válido (401)."""


def descargar_archivo(
    token: str,
    ruc_emisor: str,
    tipo_cod: str,
    serie: str,
    numero: int | str,
    tipo_archivo: str,
    origen: str = _ORIGEN_RECIBIDO,
    intentos: int = 6,
) -> tuple[str, bytes] | None:
    """Descarga un archivo del comprobante. Devuelve (nombre, bytes) o None si no
    está disponible (404). Reintenta ante 500/desconexión. Lanza TokenExpirado (401).

    tipo_cod: código SUNAT de 2 dígitos ('01' factura, '03' boleta, '07' NC, '08' ND).
    tipo_archivo: 'pdf' | 'xml' | 'cdr'.
    """
    ident = f"{ruc_emisor}-{tipo_cod}-{serie}-{numero}-{origen}"
    url = f"{_BASE}/{ident}/{_COD[tipo_archivo]}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
    }
    for _ in range(intentos):
        try:
            resp = httpx.get(url, headers=headers, timeout=60)
        except httpx.HTTPError:
            time.sleep(2)  # desconexión del WAF -> reintentar
            continue
        if resp.status_code == 200:
            return _extraer(resp.json())
        if resp.status_code == 401:
            raise TokenExpirado()
        if resp.status_code == 404:
            return None  # el comprobante no tiene ese archivo disponible
        if resp.status_code == 500:
            time.sleep(2)  # 500 intermitente de SUNAT -> reintentar
            continue
        return None
    return None


def _extraer(data: dict) -> tuple[str, bytes] | None:
    """{nomArchivo, valArchivo(base64 de un ZIP)} -> (nombre_interno, bytes)."""
    val = data.get("valArchivo")
    if not val:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(val))) as z:
            nombres = z.namelist()
            if not nombres:
                return None
            return nombres[0], z.read(nombres[0])
    except (ValueError, zipfile.BadZipFile):
        return None
