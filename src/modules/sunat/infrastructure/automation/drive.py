import os
import re

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from src.platform.config.settings import settings

# Scope amplio a proposito: se necesita para leer un Excel arbitrario del Drive
# del usuario via enlace (drive.file no lo permite). Tokens cifrados en reposo.
SCOPES = ["https://www.googleapis.com/auth/drive"]


def extraer_id(url_o_id: str) -> str:
    for pattern in [
        r"/folders/([a-zA-Z0-9_-]+)",
        r"/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
    ]:
        m = re.search(pattern, url_o_id)
        if m:
            return m.group(1)
    return url_o_id.strip()


def _build_service(access_token: str, refresh_token: str = ""):
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token or None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


class DriveClient:
    """Construye el servicio de Drive una sola vez y lo reutiliza para todas las
    subidas de un job (evita reconstruir/refrescar credenciales en cada archivo).
    Si se pasa `on_refresh`, persiste el access token cada vez que cambie."""

    def __init__(self, access_token: str, refresh_token: str = "", on_refresh=None):
        self._creds = Credentials(
            token=access_token,
            refresh_token=refresh_token or None,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            scopes=SCOPES,
        )
        self._on_refresh = on_refresh
        self._last_token = access_token
        if not self._creds.valid and self._creds.refresh_token:
            self._creds.refresh(Request())
            self._maybe_persist()
        self._service = build("drive", "v3", credentials=self._creds)

    def _maybe_persist(self):
        # googleapiclient refresca las credenciales in-place ante un 401; si el
        # token cambio, lo persistimos una vez.
        token = self._creds.token
        if self._on_refresh and token and token != self._last_token:
            self._last_token = token
            try:
                self._on_refresh(token)
            except Exception:
                pass

    def subir_archivo(self, folder_id: str, file_path: str) -> str:
        nombre = os.path.basename(file_path)
        mime = "application/pdf" if file_path.endswith(".pdf") else "text/xml"
        f = self._service.files().create(
            body={"name": nombre, "parents": [folder_id]},
            media_body=MediaFileUpload(file_path, mimetype=mime, resumable=False),
            fields="id",
        ).execute()
        self._maybe_persist()
        return f.get("id", "")


def descargar_excel(url_o_id: str, dest_path: str, access_token: str, refresh_token: str = ""):
    service = _build_service(access_token, refresh_token)
    file_id = extraer_id(url_o_id)
    mime = service.files().get(fileId=file_id, fields="mimeType").execute().get("mimeType", "")
    if mime == "application/vnd.google-apps.spreadsheet":
        request = service.files().export_media(
            fileId=file_id,
            mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
