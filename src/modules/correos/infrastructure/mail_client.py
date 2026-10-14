"""
Clientes de buzón (Gmail API y Microsoft Graph), SYNC.

Listan correos con adjuntos por rango de fecha y descargan los adjuntos con un tope
de bytes. Portado del standalone a httpx sync, conservando: la consulta de Gmail
(`has:attachment after:/before:`), el filtro de Graph (`hasAttachments eq true`),
el recorrido del árbol MIME de Gmail, la decodificación base64url y los topes de
tamaño. Reintento simple ante 429/5xx (respeta Retry-After); los errores de
credencial (401/403) no se reintentan.
"""
import base64
import binascii
import json
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0)
_MAX_REINTENTOS = 4
_CHUNK = 64 * 1024
_FACTOR_BASE64 = 1.40  # Gmail entrega base64 en JSON: el tope crudo lo contempla


class MailError(Exception):
    """Fallo del proveedor de correo."""

    def __init__(self, message: str, *, revoked: bool = False, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.revoked = revoked
        self.retryable = retryable


@dataclass(frozen=True)
class MessageSummary:
    provider_id: str
    sender: str
    subject: str
    received_at: datetime | None


@dataclass(frozen=True)
class AttachmentRef:
    attachment_id: str
    name: str
    declared_mime: str
    declared_size: int


class _MailClient:
    _nombre = ""

    def _get(self, client: httpx.Client, url: str, token: str, params: dict | None = None) -> dict:
        for intento in range(_MAX_REINTENTOS):
            try:
                resp = client.get(
                    url, params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                if intento == _MAX_REINTENTOS - 1:
                    raise MailError("Error de red con el proveedor", retryable=True) from exc
                time.sleep(2**intento)
                continue
            if resp.status_code == httpx.codes.OK:
                cuerpo = resp.json()
                return cuerpo if isinstance(cuerpo, dict) else {}
            self._raise_or_retry(resp, intento)
            time.sleep(_retry_after(resp) or 2**intento)
        raise MailError("El proveedor no respondió", retryable=True)

    def _raise_or_retry(self, resp: httpx.Response, intento: int) -> None:
        code = resp.status_code
        if code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise MailError("El acceso al buzón fue revocado o caducó.", revoked=True)
        if code == httpx.codes.TOO_MANY_REQUESTS or code >= httpx.codes.INTERNAL_SERVER_ERROR:
            if intento == _MAX_REINTENTOS - 1:
                raise MailError("El proveedor está saturado.", retryable=True)
            return  # reintentable
        raise MailError(f"El proveedor devolvió un error ({code}).")


def _retry_after(resp: httpx.Response) -> int | None:
    valor = resp.headers.get("Retry-After")
    if not valor:
        return None
    try:
        return max(1, min(30, int(float(valor))))
    except ValueError:
        return None


class GmailClient(_MailClient):
    _nombre = "gmail"
    _BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

    def listar_mensajes(
        self, token: str, *, desde: date | None, hasta: date | None, limite: int, carpeta: str
    ) -> list[MessageSummary]:
        consulta = self._consulta(desde, hasta, carpeta)
        entregados: list[MessageSummary] = []
        page_token: str | None = None
        with httpx.Client(timeout=_TIMEOUT) as client:
            while len(entregados) < limite:
                params: dict = {"q": consulta, "maxResults": min(100, limite - len(entregados))}
                if page_token:
                    params["pageToken"] = page_token
                pagina = self._get(client, f"{self._BASE}/messages", token, params)
                mensajes = pagina.get("messages", [])
                if not mensajes:
                    break
                for ref in mensajes:
                    if len(entregados) >= limite:
                        break
                    detalle = self._get(
                        client, f"{self._BASE}/messages/{ref['id']}", token,
                        {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                    )
                    entregados.append(self._a_resumen(ref["id"], detalle))
                page_token = pagina.get("nextPageToken")
                if not page_token:
                    break
        return entregados

    def listar_adjuntos(self, token: str, message_id: str) -> list[AttachmentRef]:
        with httpx.Client(timeout=_TIMEOUT) as client:
            detalle = self._get(
                client, f"{self._BASE}/messages/{message_id}", token, {"format": "full"}
            )
        return self._recorrer_partes(detalle.get("payload", {}))

    def descargar_adjunto(
        self, token: str, *, message_id: str, attachment_id: str, limite_bytes: int
    ) -> bytes:
        tope = int(limite_bytes * _FACTOR_BASE64) + 1024
        url = f"{self._BASE}/messages/{message_id}/attachments/{attachment_id}"
        crudo = _descargar_crudo(url, token, tope)
        try:
            cuerpo = json.loads(crudo)
            datos = cuerpo.get("data", "")
            relleno = "=" * (-len(datos) % 4)
            return base64.urlsafe_b64decode(datos + relleno)
        except (ValueError, binascii.Error) as exc:
            raise MailError("No se pudo decodificar el adjunto.") from exc

    @staticmethod
    def _consulta(desde: date | None, hasta: date | None, carpeta: str) -> str:
        partes = ["has:attachment"]
        if carpeta and carpeta.upper() != "INBOX":
            partes.append(f'label:"{carpeta}"')
        else:
            partes.append("in:inbox")
        if desde:
            partes.append(f"after:{desde:%Y/%m/%d}")
        if hasta:
            partes.append(f"before:{hasta:%Y/%m/%d}")
        return " ".join(partes)

    @staticmethod
    def _a_resumen(identificador: str, detalle: dict) -> MessageSummary:
        cabeceras = {
            c.get("name", "").lower(): c.get("value", "")
            for c in detalle.get("payload", {}).get("headers", [])
        }
        recibido = None
        marca = detalle.get("internalDate")
        if marca:
            try:
                recibido = datetime.fromtimestamp(int(marca) / 1000, tz=UTC)
            except (ValueError, OSError):
                recibido = None
        return MessageSummary(
            provider_id=identificador,
            sender=cabeceras.get("from", "")[:320],
            subject=cabeceras.get("subject", ""),
            received_at=recibido,
        )

    @staticmethod
    def _recorrer_partes(parte: dict) -> list[AttachmentRef]:
        resultado: list[AttachmentRef] = []

        def recorrer(nodo: dict, profundidad: int = 0) -> None:
            if profundidad > 10:
                return
            cuerpo = nodo.get("body", {})
            id_adjunto = cuerpo.get("attachmentId")
            nombre = nodo.get("filename", "")
            if id_adjunto and nombre:
                resultado.append(AttachmentRef(
                    attachment_id=id_adjunto, name=nombre,
                    declared_mime=nodo.get("mimeType", ""),
                    declared_size=int(cuerpo.get("size", 0)),
                ))
            for hijo in nodo.get("parts", []):
                recorrer(hijo, profundidad + 1)

        recorrer(parte)
        return resultado


class GraphClient(_MailClient):
    _nombre = "outlook"
    _BASE = "https://graph.microsoft.com/v1.0/me"

    def listar_mensajes(
        self, token: str, *, desde: date | None, hasta: date | None, limite: int, carpeta: str
    ) -> list[MessageSummary]:
        filtros = ["hasAttachments eq true"]
        if desde:
            filtros.append(f"receivedDateTime ge {desde:%Y-%m-%d}T00:00:00Z")
        if hasta:
            filtros.append(f"receivedDateTime le {hasta:%Y-%m-%d}T23:59:59Z")
        url: str | None = f"{self._BASE}/mailFolders/{carpeta or 'inbox'}/messages"
        params: dict | None = {
            "$filter": " and ".join(filtros),
            "$select": "id,subject,from,receivedDateTime",
            "$top": min(50, limite),
        }
        entregados: list[MessageSummary] = []
        with httpx.Client(timeout=_TIMEOUT) as client:
            while url and len(entregados) < limite:
                pagina = self._get(client, url, token, params)
                for crudo in pagina.get("value", []):
                    if len(entregados) >= limite:
                        break
                    entregados.append(self._a_resumen(crudo))
                url = pagina.get("@odata.nextLink", "")
                params = None  # el nextLink ya trae los parámetros
        return entregados

    def listar_adjuntos(self, token: str, message_id: str) -> list[AttachmentRef]:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = self._get(
                client, f"{self._BASE}/messages/{message_id}/attachments", token,
                {"$select": "id,name,contentType,size"},
            )
        referencias = []
        for crudo in resp.get("value", []):
            # Solo adjuntos de fichero (no correos incrustados ni enlaces → anti-SSRF).
            if crudo.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            referencias.append(AttachmentRef(
                attachment_id=str(crudo.get("id", "")), name=str(crudo.get("name", "")),
                declared_mime=str(crudo.get("contentType", "")),
                declared_size=int(crudo.get("size", 0)),
            ))
        return referencias

    def descargar_adjunto(
        self, token: str, *, message_id: str, attachment_id: str, limite_bytes: int
    ) -> bytes:
        url = f"{self._BASE}/messages/{message_id}/attachments/{attachment_id}/$value"
        return _descargar_crudo(url, token, limite_bytes, follow_redirects=True)

    @staticmethod
    def _a_resumen(crudo: dict) -> MessageSummary:
        desde = crudo.get("from")
        remitente = ""
        if isinstance(desde, dict):
            remitente = desde.get("emailAddress", {}).get("address", "")
        recibido = None
        marca = crudo.get("receivedDateTime")
        if marca:
            try:
                recibido = datetime.fromisoformat(marca.replace("Z", "+00:00"))
            except ValueError:
                recibido = None
        return MessageSummary(
            provider_id=str(crudo.get("id", "")),
            sender=str(remitente)[:320],
            subject=str(crudo.get("subject", "")),
            received_at=recibido,
        )


def _descargar_crudo(url: str, token: str, tope: int, *, follow_redirects: bool = False) -> bytes:
    """Descarga en streaming y corta al superar `tope` bytes (anti-bomba/costo)."""
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=follow_redirects) as client:
        with client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as resp:
            if resp.status_code != httpx.codes.OK:
                resp.read()
                if resp.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
                    raise MailError("El acceso al buzón fue revocado o caducó.", revoked=True)
                raise MailError("No se pudo descargar el adjunto.", retryable=True)
            crudo = bytearray()
            for trozo in resp.iter_bytes(_CHUNK):
                crudo.extend(trozo)
                if len(crudo) > tope:
                    return bytes(crudo)  # se corta; el validador lo rechazará por tamaño
    return bytes(crudo)


def cliente_de(provider: str) -> _MailClient:
    if provider == "gmail":
        return GmailClient()
    if provider == "outlook":
        return GraphClient()
    raise MailError(f"Proveedor no soportado: {provider}")
