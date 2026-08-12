import httpx
from src.modules.sire.infrastructure.sunat.base import (
    SIRE_BASE, TicketFileInfo, _auth_headers, poll_ticket, download_file,
    consultar_ticket, solicitar_export,
)

COD_LIBRO_COMPRAS = "080000"

ENDPOINTS = {
    "anio_mes":       f"{SIRE_BASE}/rvierce/padron/web/omisos/{{cod_libro}}/periodos",
    "exportar_prop":  f"{SIRE_BASE}/rce/propuesta/web/propuesta/{{periodo}}/exportacioncomprobantepropuesta",
    "estado_ticket":  f"{SIRE_BASE}/rvierce/gestionprocesosmasivos/web/masivo/consultaestadotickets",
    "descargar_arch": f"{SIRE_BASE}/rvierce/gestionprocesosmasivos/web/masivo/archivoreporte",
}


async def get_periodos_compras(token: str) -> list[dict]:
    headers = _auth_headers(token)
    url = ENDPOINTS["anio_mes"].format(cod_libro=COD_LIBRO_COMPRAS)
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
    return resp.json()


async def solicitar_export_compras(get_token, periodo: str) -> str:
    """
    Fase 1 RCE: GET exportacioncomprobantepropuesta → numTicket.
    Maneja 401 (renovar token), 429 (límite de SUNAT → espera y reintenta) y
    42209 (proceso en curso → reutiliza ese ticket).
    """
    url = ENDPOINTS["exportar_prop"].format(periodo=periodo)
    params_exp = {"codTipoArchivo": "0", "codOrigenEnvio": "2"}
    return await solicitar_export(get_token, url, params_exp, f"compras periodo {periodo}")


async def consultar_ticket_compras(get_token, num_ticket: str, periodo: str) -> tuple[str, TicketFileInfo] | None:
    """Consulta única del estado de un ticket de compras. None si SUNAT ya no lo conoce."""
    return await consultar_ticket(
        get_token, ENDPOINTS["estado_ticket"], num_ticket, periodo, COD_LIBRO_COMPRAS,
    )


async def descargar_ticket_compras(get_token, num_ticket: str, periodo: str) -> str:
    """
    Fases 2-3 RCE: polling del ticket hasta Terminado + descarga del ZIP.
    Devuelve la RUTA a un archivo temporal con el TXT (el llamador lo borra).
    """
    info = await poll_ticket(
        get_token,
        ENDPOINTS["estado_ticket"],
        num_ticket,
        periodo,
        COD_LIBRO_COMPRAS,
    )
    return await download_file(
        get_token,
        ENDPOINTS["descargar_arch"],
        info,
        COD_LIBRO_COMPRAS,
        num_ticket,
    )
