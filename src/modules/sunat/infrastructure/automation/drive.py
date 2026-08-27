import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from src.platform.config.settings import settings

# Scope acotado: la app solo accede a los archivos/carpetas que ella misma crea.
# No requiere la verificación de Google que exige el scope amplio 'drive', y
# reduce el radio de daño (no puede tocar el resto del Drive del usuario). El
# Excel de entrada ya no se lee del Drive: el usuario lo elige con el Picker en
# el navegador, que lo descarga y lo sube como archivo normal.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_MIME_FOLDER = "application/vnd.google-apps.folder"


class DriveClient:
    """Sube los PDF/XML de un job a una carpeta PROPIA de la app en el Drive del
    usuario (con `drive.file` la app solo ve/gestiona lo que ella crea). La
    carpeta se busca o se crea una vez por job. Si se pasa `on_refresh`, persiste
    el access token cada vez que cambie."""

    def __init__(self, access_token, refresh_token="", on_refresh=None, folder_name="SuperTax"):
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
        self._folder_id = self._asegurar_carpeta(folder_name)

    def _maybe_persist(self):
        # googleapiclient refresca las credenciales in-place ante un 401; si el
        # token cambió, lo persistimos una vez.
        token = self._creds.token
        if self._on_refresh and token and token != self._last_token:
            self._last_token = token
            try:
                self._on_refresh(token)
            except Exception:
                pass

    def _asegurar_carpeta(self, nombre: str) -> str:
        """Busca la carpeta propia de la app por nombre; si no existe, la crea.
        Con `drive.file`, `list` solo devuelve lo que la app creó, así que no hay
        colisión con carpetas del usuario que se llamen igual."""
        seguro = nombre.replace("'", " ").strip() or "SuperTax"
        query = f"mimeType='{_MIME_FOLDER}' and name='{seguro}' and trashed=false"
        res = self._service.files().list(
            q=query, spaces="drive", fields="files(id)", pageSize=1
        ).execute()
        existentes = res.get("files", [])
        if existentes:
            return existentes[0]["id"]
        carpeta = self._service.files().create(
            body={"name": seguro, "mimeType": _MIME_FOLDER}, fields="id"
        ).execute()
        self._maybe_persist()
        return carpeta["id"]

    def subir_archivo(self, file_path: str) -> str:
        nombre = os.path.basename(file_path)
        mime = "application/pdf" if file_path.endswith(".pdf") else "text/xml"
        f = self._service.files().create(
            body={"name": nombre, "parents": [self._folder_id]},
            media_body=MediaFileUpload(file_path, mimetype=mime, resumable=False),
            fields="id",
        ).execute()
        self._maybe_persist()
        return f.get("id", "")
