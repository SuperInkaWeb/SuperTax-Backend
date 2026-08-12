import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import httpx
import zipfile
import io
from dataclasses import dataclass, field
from enum import Enum

from src.platform.config.settings import settings

logger = logging.getLogger(__name__)

_7ZIP_PATHS = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
    "7z",
    "7za",
]

def _find_7zip() -> str | None:
    for path in _7ZIP_PATHS:
        if os.path.isabs(path):
            if os.path.exists(path):
                return path
        else:
            if shutil.which(path):
                return path
    return None

SIRE_BASE = "https://api-sire.sunat.gob.pe/v1/contribuyente/migeigv/libros"
POLL_INTERVAL_SECONDS = 10
DOWNLOAD_MAX_ATTEMPTS = 4
DOWNLOAD_RETRY_PAUSE_SECONDS = 15
# SUNAT limita la frecuencia de solicitudes de propuesta (HTTP 429). Ante 429
# se espera (respetando Retry-After si viene) y se reintenta.
EXPORT_MAX_INTENTOS_429 = 6
EXPORT_BACKOFF_BASE_SECONDS = 15


class TicketStatus(str, Enum):
    en_proceso = "En Proceso"
    terminado  = "Terminado"
    error      = "Error"


@dataclass
class TicketFileInfo:
    """Información del archivo generado por SUNAT al terminar el ticket."""
    nom_archivo:      str
    cod_tipo_archivo: str | None
    cod_proceso:      str | None
    per_tributario:   str
    archivo_reportes: list[dict] = field(default_factory=list)


def _extract_ticket(resp: "httpx.Response", context: str) -> str:
    """
    Extrae el numTicket de la respuesta de exportapropuesta.
    Si SUNAT devuelve 422 con error 42209 (proceso en curso), reutiliza ese ticket.
    """
    data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}

    if resp.status_code == 200:
        ticket = data.get("numTicket")
        if not ticket:
            raise ValueError(f"SUNAT no devolvió numTicket para {context}: {data}")
        return ticket

    if resp.status_code == 422:
        errors = data.get("errors", [])
        cod = errors[0].get("cod") if errors else None
        if cod == 42209:
            msg = errors[0].get("msg", "")
            match = re.search(r"Ticket:\s*(\d+)", msg)
            if match:
                return match.group(1)
        raise ValueError(f"SUNAT 422 para {context}: {data}")

    resp.raise_for_status()
    raise ValueError(f"Respuesta inesperada {resp.status_code} para {context}")


def _auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


def _retry_after_segundos(resp: "httpx.Response") -> int | None:
    ra = resp.headers.get("Retry-After", "").strip()
    return int(ra) if ra.isdigit() else None


async def solicitar_export(get_token, url: str, params: dict, context: str) -> str:
    """
    GET a exportapropuesta con reintentos. Maneja:
      - 401: renueva el token y reintenta;
      - 429: SUNAT limita la frecuencia de solicitudes de propuesta; espera
        (Retry-After si viene, si no un backoff creciente) y reintenta.
    Devuelve el numTicket. Centraliza el comportamiento para compras y ventas.
    """
    token = await get_token(False)
    async with httpx.AsyncClient(timeout=120) as client:
        resp = None
        for intento in range(EXPORT_MAX_INTENTOS_429):
            resp = await client.get(url, headers=_auth_headers(token), params=params)
            if resp.status_code == 401:
                token = await get_token(True)
                resp = await client.get(url, headers=_auth_headers(token), params=params)
            if resp.status_code == 429 and intento < EXPORT_MAX_INTENTOS_429 - 1:
                espera = _retry_after_segundos(resp) or EXPORT_BACKOFF_BASE_SECONDS * (intento + 1)
                logger.warning(
                    "SUNAT 429 (límite de solicitudes) en %s; espero %ss y reintento (%s/%s)",
                    context, espera, intento + 1, EXPORT_MAX_INTENTOS_429 - 1,
                )
                await asyncio.sleep(espera)
                continue
            break
    return _extract_ticket(resp, context)


def _extract_file_info(registro: dict, periodo: str) -> TicketFileInfo:
    """
    Extrae nombre de archivo y metadatos del registro de ticket.
    Manual 5.16/5.31: el archivo está en archivoReporte[] o en detalleTicket.
    Soporta ZIP particionado: archivoReporte[] puede tener múltiples entradas.
    """
    nom_archivo      = None
    cod_tipo_archivo = None
    archivo_reportes = registro.get("archivoReporte") or []

    if archivo_reportes:
        first            = archivo_reportes[0]
        nom_archivo      = first.get("nomArchivoReporte")
        cod_tipo_archivo = (
            first.get("codTipoArchivoReporte")
            or first.get("codTipoAchivoReporte")
        )

    if not nom_archivo:
        detalle = registro.get("detalleTicket") or {}
        nom_archivo = detalle.get("nomArchivoReporte")

    if not nom_archivo:
        nom_archivo = registro.get("nomArchivoDescarga") or registro.get("nomArchivoImportacion")

    return TicketFileInfo(
        nom_archivo      = nom_archivo or "",
        cod_tipo_archivo = cod_tipo_archivo,
        cod_proceso      = registro.get("codProceso"),
        per_tributario   = registro.get("perTributario") or periodo,
        archivo_reportes = archivo_reportes,
    )


def _get_estado(registro: dict) -> str:
    """
    Extrae el estado del ticket. El campo varía entre respuestas:
    - detalleTicket.desEstadoEnvio  → "Terminado"
    - desEstadoProceso              → "Terminado" / "En Proceso"
    - desEstado                     → legacy
    """
    detalle = registro.get("detalleTicket") or {}
    return (
        detalle.get("desEstadoEnvio")
        or registro.get("desEstadoProceso")
        or registro.get("desEstado")
        or ""
    )


async def consultar_ticket(
    get_token,
    ticket_url: str,
    num_ticket: str,
    periodo: str,
    cod_libro: str,
) -> tuple[str, TicketFileInfo] | None:
    """
    Consulta ÚNICA del estado de un ticket (sin polling).
    Devuelve (estado, TicketFileInfo) o None si SUNAT ya no conoce el ticket.
    """
    token = await get_token(False)
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(
            ticket_url,
            headers=_auth_headers(token),
            params={
                "perIni":         periodo,
                "perFin":         periodo,
                "page":           1,
                "perPage":        20,
                "codLibro":       cod_libro,
                "codOrigenEnvio": "2",
                "numTicket":      num_ticket,
            }
        )
        if resp.status_code == 401:
            token = await get_token(True)
            resp = await client.get(
                ticket_url,
                headers=_auth_headers(token),
                params={
                    "perIni":         periodo,
                    "perFin":         periodo,
                    "page":           1,
                    "perPage":        20,
                    "codLibro":       cod_libro,
                    "codOrigenEnvio": "2",
                    "numTicket":      num_ticket,
                }
            )
        resp.raise_for_status()

    registros = resp.json().get("registros", [])
    if not registros:
        return None
    registro = registros[0]
    return _get_estado(registro), _extract_file_info(registro, periodo)


async def poll_ticket(
    get_token,
    ticket_url: str,
    num_ticket: str,
    periodo: str,
    cod_libro: str,
) -> TicketFileInfo:
    """
    Hace polling hasta que el ticket esté Terminado.
    Devuelve TicketFileInfo con nombre de archivo y metadatos de descarga.
    Manual SIRE Ventas 5.16 / Compras 5.31.

    get_token: async callable (force_refresh: bool) -> str.
    El polling puede durar hasta SUNAT_POLL_TIMEOUT_MINUTES; SUNAT invalida el
    token si se emite otro para el mismo usuario SOL — ante un 401 se renueva.

    El límite es de tiempo de reloj (no de número de intentos): así un SUNAT
    lento que tarda en responder cada consulta no puede estirar la espera a
    horas — corta al cumplirse el tope con un error claro.
    """
    timeout_seconds = settings.SUNAT_POLL_TIMEOUT_MINUTES * 60
    inicio = time.monotonic()
    while time.monotonic() - inicio < timeout_seconds:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

        token = await get_token(False)
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(
                    ticket_url,
                    headers=_auth_headers(token),
                    params={
                        "perIni":         periodo,
                        "perFin":         periodo,
                        "page":           1,
                        "perPage":        20,
                        "codLibro":       cod_libro,
                        "codOrigenEnvio": "2",
                        "numTicket":      num_ticket,
                    }
                )
        except httpx.TransportError:
            continue

        if resp.status_code == 401:
            await get_token(True)
            continue
        if resp.status_code >= 500:
            continue
        resp.raise_for_status()

        registros = resp.json().get("registros", [])
        if not registros:
            continue

        registro = registros[0]
        estado   = _get_estado(registro)

        if "terminado" in estado.lower():
            return _extract_file_info(registro, periodo)

        if "error" in estado.lower():
            raise ValueError(f"SUNAT devolvió error en ticket {num_ticket}: {registro}")

    raise TimeoutError(
        f"SUNAT no terminó de generar la propuesta en "
        f"{settings.SUNAT_POLL_TIMEOUT_MINUTES} minutos (ticket {num_ticket}). "
        f"Puede estar lento o saturado; reintenta más tarde."
    )


async def download_file(
    get_token,
    download_url: str,
    info: TicketFileInfo,
    cod_libro: str,
    num_ticket: str,
) -> str:
    """
    Descarga el ZIP generado por SUNAT y devuelve la RUTA a un archivo temporal
    con el TXT extraído. Manual SIRE Ventas 5.17 / Compras 5.32.

    La descarga es en streaming a disco: la RAM del proceso no crece con el
    tamaño del archivo (importante para propuestas de millones de filas).
    Soporta ZIP particionado (.z01, .z02, ..., .zip).

    El llamador es responsable de borrar el archivo temporal devuelto.
    get_token: async callable (force_refresh: bool) -> str; renueva ante 401.
    """
    partes = info.archivo_reportes if info.archivo_reportes else [
        {
            "nomArchivoReporte":   info.nom_archivo,
            "codTipoAchivoReporte": info.cod_tipo_archivo,
        }
    ]

    workdir = tempfile.mkdtemp(prefix="sunat_dl_")
    try:
        part_files: list[str] = []
        for parte in partes:
            nombre   = parte.get("nomArchivoReporte", "")
            cod_tipo = parte.get("codTipoAchivoReporte") or parte.get("codTipoArchivoReporte")

            params = {
                "nomArchivoReporte": nombre,
                "codLibro":          cod_libro,
                "perTributario":     info.per_tributario,
                "numTicket":         num_ticket,
            }
            if cod_tipo is not None:
                params["codTipoArchivoReporte"] = cod_tipo
            if info.cod_proceso is not None:
                params["codProceso"] = info.cod_proceso

            dest = os.path.join(workdir, nombre or f"parte_{len(part_files)}")
            await _descargar_parte_a_disco(get_token, download_url, params, dest)
            part_files.append(dest)

        return await asyncio.to_thread(_extraer_txt_a_archivo, part_files)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _descargar_parte_a_disco(get_token, download_url: str, params: dict, dest_path: str) -> None:
    """
    Descarga una parte del reporte en streaming directo a disco (RAM constante),
    con reintentos ante caídas transitorias de SUNAT (5xx / red) y renovación de
    token ante 401. Si se agotan los intentos, propaga el último error.
    """
    ultimo_error: Exception | None = None
    for intento in range(DOWNLOAD_MAX_ATTEMPTS):
        token = await get_token(False)
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream(
                    "GET", download_url, headers=_auth_headers(token), params=params
                ) as resp:
                    if resp.status_code == 401:
                        await get_token(True)
                        continue
                    if resp.status_code >= 500:
                        ultimo_error = httpx.HTTPStatusError(
                            f"SUNAT respondió {resp.status_code} al descargar el reporte",
                            request=resp.request, response=resp,
                        )
                        await asyncio.sleep(DOWNLOAD_RETRY_PAUSE_SECONDS)
                        continue
                    if resp.status_code >= 400:
                        await resp.aread()
                        resp.raise_for_status()

                    with open(dest_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(1024 * 1024):
                            f.write(chunk)
                    return
        except httpx.TransportError as exc:
            ultimo_error = exc
            await asyncio.sleep(DOWNLOAD_RETRY_PAUSE_SECONDS)
            continue

    raise ValueError(
        f"SUNAT no pudo entregar el archivo de la propuesta tras {DOWNLOAD_MAX_ATTEMPTS} "
        f"intentos. Puede estar lento o saturado; reintenta más tarde."
    ) from ultimo_error


def _extraer_txt_a_archivo(part_files: list[str]) -> str:
    """
    Extrae el TXT del ZIP (simple o particionado) a un archivo temporal
    persistente y devuelve su ruta. Trabaja sobre disco: no carga el ZIP en RAM.
    Corre en un hilo aparte.
    """
    fd, txt_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        if len(part_files) == 1:
            with zipfile.ZipFile(part_files[0]) as zf:
                nombres = zf.namelist()
                txts = [f for f in nombres if f.lower().endswith(".txt")]
                target = txts[0] if txts else (nombres[0] if nombres else None)
                if not target:
                    raise ValueError("El ZIP de SUNAT no contiene ningún archivo")
                with zf.open(target) as src, open(txt_path, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
            return txt_path

        bin_7z = _find_7zip()
        if not bin_7z:
            raise RuntimeError(
                "ZIP particionado recibido de SUNAT pero 7-zip no está instalado. "
                "Windows: instalar https://www.7-zip.org/ | Linux: apt install p7zip-full"
            )

        part_files.sort(key=lambda p: os.path.splitext(p)[1].lower())
        workdir = os.path.dirname(part_files[0])
        result = subprocess.run(
            [bin_7z, "e", part_files[0], f"-o{workdir}", "-y", "-aoa"],
            capture_output=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"7-zip falló ({result.returncode}): {result.stderr.decode(errors='replace')[:500]}"
            )

        nombres_partes = {os.path.basename(p) for p in part_files}
        extraidos = [
            f for f in os.listdir(workdir)
            if f not in nombres_partes and not re.match(r".*\.(z\d+|zip)$", f.lower())
        ]
        txts = [f for f in extraidos if f.lower().endswith(".txt")]
        elegido = txts[0] if txts else (extraidos[0] if extraidos else None)
        if not elegido:
            raise ValueError("7-zip no extrajo ningún archivo")
        os.replace(os.path.join(workdir, elegido), txt_path)
        return txt_path
    except Exception:
        try:
            os.remove(txt_path)
        except OSError:
            pass
        raise
