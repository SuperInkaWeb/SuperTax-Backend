import os

from playwright.sync_api import TimeoutError as PWTimeoutError

from src.modules.sunat.infrastructure.automation.correo import enviar_individual

from .selectores import (
    WAIT_LIMPIAR_MS,
    WAIT_PDF_XML_MS,
    XPATH_CERRAR,
    XPATH_CONSULTAR,
    XPATH_LIMPIAR,
    XPATH_NUMERO,
    XPATH_PDF,
    XPATH_RUC_EMISOR,
    XPATH_SERIE,
    XPATH_TIPO_DROPDOWN,
    XPATH_TIPO_FILTRO,
    XPATH_TIPO_ITEM,
    XPATH_XML,
    DriveTokenExpirado,
    RefreshNecesario,
)
from .navegacion import (
    _dismiss_modal_sesion,
    _esperar_sin_cargando,
    _hay_cargando,
    _hay_error_servidor,
)


def _procesar_comprobante(frame_app, page, tipo_texto, ruc_emisor, serie, numero,
                          config, drive_client, drive_folder_id, agrupados, log,
                          flags=None):
    """Rellena el formulario de consulta y descarga PDF/XML segun flags.

    flags keys:
      "pdf": bool           — descargar PDF (default True)
      "xml": bool           — descargar XML (default True)
      "contexto": str       — "filtro" | "forzar" (para mensajes de log)
      "_solo_descarga": bool — si True, omite Drive y correo (el caller los maneja)

    Retorna {"pdf": bool, "xml": bool, "ruta_pdf": str|None, "ruta_xml": str|None}.
    Lanza RefreshNecesario o Exception para que el caller aplique reintentos.
    """
    if flags is None:
        flags = {}
    hacer_pdf     = flags.get("pdf", True)
    hacer_xml     = flags.get("xml", True)
    solo_descarga = flags.get("_solo_descarga", False)

    _dismiss_modal_sesion(page, log)
    ruc_input = frame_app.locator(f"xpath={XPATH_RUC_EMISOR}").first
    ruc_input.wait_for(timeout=90000)
    ruc_input.fill(ruc_emisor)

    frame_app.locator(f"xpath={XPATH_TIPO_DROPDOWN}").first.evaluate("el => el.click()")

    filtro = frame_app.locator(f"xpath={XPATH_TIPO_FILTRO}").first
    filtro.wait_for(timeout=10000)
    filtro.fill(tipo_texto)

    item_loc = frame_app.locator(
        f"xpath={XPATH_TIPO_ITEM.format(tipo=tipo_texto)}"
    ).first
    item_loc.wait_for(timeout=10000)
    item_loc.evaluate("el => el.click()")

    frame_app.locator(f"xpath={XPATH_SERIE}").first.fill(serie)
    frame_app.locator(f"xpath={XPATH_NUMERO}").first.fill(str(numero))

    frame_app.locator(f"xpath={XPATH_CONSULTAR}").first.click()
    try:
        frame_app.locator(f"xpath={XPATH_PDF}").first.wait_for(state="visible", timeout=500)
    except PWTimeoutError:
        pass  # normal — el resultado tarda mas; el wait_for de 90 s lo captura

    if _hay_error_servidor(frame_app):
        raise RefreshNecesario("Error del Servidor tras Consultar")

    try:
        frame_app.locator(f"xpath={XPATH_PDF}").first.wait_for(timeout=90000)
    except PWTimeoutError:
        if _hay_error_servidor(frame_app):
            raise RefreshNecesario("Error del Servidor esperando resultado")
        _esperar_sin_cargando(frame_app, timeout_ms=5000)
        raise
    log("   [ v ] Comprobante encontrado")

    if _hay_error_servidor(frame_app):
        raise RefreshNecesario("Error del Servidor antes de descargar")

    ruta_pdf = None
    pdf_ok   = False
    if hacer_pdf:
        try:
            with page.expect_download(timeout=30000) as dl_pdf:
                frame_app.locator(f"xpath={XPATH_PDF}").first.click()
        except PWTimeoutError:
            if _hay_error_servidor(frame_app):
                raise RefreshNecesario("Error del Servidor al descargar PDF")
            raise
        ruta_pdf = os.path.join(config["descargas"], dl_pdf.value.suggested_filename)
        dl_pdf.value.save_as(ruta_pdf)
        log(f"   [ v ] PDF: {dl_pdf.value.suggested_filename}")
        pdf_ok = True
    elif not solo_descarga:
        log("   [ - ] PDF " + ("ya descargado" if flags.get("contexto") == "forzar" else "no solicitado"))

    page.wait_for_timeout(WAIT_PDF_XML_MS)
    ruta_xml = None
    xml_ok   = False
    if hacer_xml:
        try:
            xml_btn = frame_app.locator(f"xpath={XPATH_XML}").first
            try:
                xml_btn.wait_for(state="visible", timeout=8000)
            except PWTimeoutError:
                if _hay_cargando(frame_app):
                    raise RefreshNecesario("Cargando... bloqueado esperando boton XML")
                log("   [ - ] XML no disponible para este comprobante")
            else:
                if _hay_error_servidor(frame_app):
                    raise RefreshNecesario("Error del Servidor en XML")
                else:
                    try:
                        with page.expect_download(timeout=12000) as dl_xml:
                            xml_btn.click()
                        ruta_xml = os.path.join(config["descargas"], dl_xml.value.suggested_filename)
                        dl_xml.value.save_as(ruta_xml)
                        log(f"   [ v ] XML: {dl_xml.value.suggested_filename}")
                        xml_ok = True
                    except PWTimeoutError:
                        if _hay_error_servidor(frame_app):
                            raise RefreshNecesario("Error del Servidor al descargar XML")
                        elif _hay_cargando(frame_app):
                            raise RefreshNecesario("Cargando... bloqueado en descarga XML")
                        else:
                            _dismiss_modal_sesion(page, log)
                            log("   [ - ] XML no disponible para este comprobante")
        except RefreshNecesario:
            raise
        except Exception as e:
            log(f"   [ - ] XML omitido: {str(e)[:60]}")
    elif not solo_descarga:
        log("   [ - ] XML " + ("ya descargado" if flags.get("contexto") == "forzar" else "no solicitado"))

    if not solo_descarga:
        if (config.get("usar_correo") and config["gmail_user"]
                and config["gmail_pass"] and config["destino"]):
            if config["modo_correo"] == "individual":
                enviar_individual(config, serie, numero, ruta_xml, ruta_pdf)
                log(f"   [ v ] Correo enviado a {config['destino']}")
            else:
                agrupados.append((serie, numero, ruta_xml, ruta_pdf))
                log("   [ + ] Archivos acumulados para envio final")

        if drive_client:
            try:
                if ruta_pdf:
                    drive_client.subir_archivo(drive_folder_id, ruta_pdf)
                if ruta_xml:
                    drive_client.subir_archivo(drive_folder_id, ruta_xml)
                log("   [ v ] Subido a Google Drive")
            except Exception as e:
                if "invalid_grant" in str(e):
                    raise DriveTokenExpirado()
                log(f"   [ ! ] Error subiendo a Drive: {e}")

    try:
        frame_app.locator(f"xpath={XPATH_CERRAR}").first.click(timeout=5000)
    except Exception:
        pass
    try:
        frame_app.locator(f"xpath={XPATH_LIMPIAR}").first.click(timeout=5000)
        page.wait_for_timeout(WAIT_LIMPIAR_MS)
    except Exception:
        pass

    return {"pdf": pdf_ok, "xml": xml_ok, "ruta_pdf": ruta_pdf, "ruta_xml": ruta_xml}
