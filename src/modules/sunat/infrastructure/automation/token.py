"""
Extrae el Bearer token de `api-cpe` de una sesión SOL ya logueada (Playwright).

El recurso `consultacpe` no se puede habilitar en la credencial de API del
contribuyente (no está en el catálogo de SUNAT); el portal lo usa con el token de
la sesión SOL. Por eso el híbrido: login por navegador -> se toma ESE token -> se
descarga por HTTP. El token dura ~1 h (cubre un lote completo).

Estrategia de captura: interceptor de red (el token viaja en el header
Authorization de las llamadas a api-cpe, o en la respuesta del token endpoint) y,
como respaldo, un scan del sessionStorage/localStorage.
"""
import base64
import re

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def capturar_tokens(page) -> list[str]:
    """Instala interceptores en `page` y devuelve la lista (mutable) donde se
    acumulan los tokens vistos hacia api-cpe / api-seguridad. Llamar ANTES del login."""
    capturados: list[str] = []

    def on_request(req):
        auth = req.headers.get("authorization", "")
        if "api-cpe.sunat.gob.pe" in req.url and auth.lower().startswith("bearer "):
            capturados.append(auth.split(" ", 1)[1])

    def on_response(resp):
        if "oauth2/token" in resp.url or "api-seguridad" in resp.url:
            try:
                tok = resp.json().get("access_token")
                if tok:
                    capturados.append(tok)
            except Exception:
                pass

    page.on("request", on_request)
    page.on("response", on_response)
    return capturados


def _es_token_cpe(tok: str) -> bool:
    """True si el payload del JWT menciona api-cpe/consultacpe (el token que sirve)."""
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        blob = base64.urlsafe_b64decode(payload).decode("utf-8", "replace").lower()
        return "api-cpe" in blob or "consultacpe" in blob
    except Exception:
        return False


def extraer_token(page, capturados: list[str]) -> str | None:
    """Elige el mejor token: 1) los capturados en red; 2) scan de storage."""
    for tok in capturados:
        if _es_token_cpe(tok):
            return tok
    if capturados:
        return capturados[-1]  # el más reciente
    try:
        raw = page.evaluate("() => JSON.stringify({s:{...sessionStorage}, l:{...localStorage}})")
        jwts = _JWT.findall(raw)
        for tok in jwts:
            if _es_token_cpe(tok):
                return tok
        return jwts[0] if jwts else None
    except Exception:
        return None
