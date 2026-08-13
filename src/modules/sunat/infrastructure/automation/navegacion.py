from playwright.sync_api import TimeoutError as PWTimeoutError

from .selectores import (
    CSS_IFRAME_APP,
    CSS_IFRAME_VCE,
    DEBUG,
    WAIT_ANGULAR_FILTRO_MS,
    WAIT_JS_CLICK_MS,
    WAIT_MENU_EXPAND_MS,
    WAIT_MODAL_CLOSE_MS,
    XPATH_CARGANDO,
    XPATH_CERRAR,
    XPATH_CONTINUAR_SIN_CONFIRMAR,
    XPATH_ERROR_SERVIDOR,
    XPATH_ERROR_SERVIDOR_ACEPTAR,
    XPATH_EMPRESAS,
    XPATH_FILTRO_RECIBIDO,
    XPATH_FINALIZAR,
    XPATH_LIMPIAR,
    XPATH_MODAL_ACEPTAR,
    XPATH_NUEVA_CONSULTA,
    XPATH_RUC_EMISOR,
    RefreshNecesario,
)


def _wire_debug(pg, log):
    """Registra navegaciones, request failures y errores JS (solo si SUNAT_DEBUG=1)."""
    if not DEBUG:
        return

    def on_nav(fr):
        if fr.parent_frame is None:
            log(f"   [nav] {fr.url}")

    def on_reqfail(req):
        if "sunat" in req.url:
            log(f"   [req-fail] {req.url}")

    def on_pageerr(err):
        log(f"   [js-error] {str(err)[:200]}")
    pg.on("framenavigated", on_nav)
    pg.on("requestfailed", on_reqfail)
    pg.on("pageerror", on_pageerr)


def _js_click(selector, descripcion, page, log, target=None, timeout=10000):
    """Click via JS para bypassear checks de visibilidad dentro de iframes.
    Devuelve True si el elemento aparecio y se clickeo, False si no."""
    t = target or page
    try:
        loc = t.locator(f"xpath={selector}").first
        loc.wait_for(state="attached", timeout=timeout)
        loc.evaluate("el => el.click()")
        log(f"   [ v ] {descripcion}")
        page.wait_for_timeout(WAIT_JS_CLICK_MS)
        return True
    except Exception:
        log(f"   [ - ] {descripcion} no aparecio")
        return False


def _dismiss_modal_sesion(page, log):
    """Detecta y cierra el modal de sesion que SUNAT muestra periodicamente (boton Aceptar)."""
    try:
        page.wait_for_selector(CSS_IFRAME_VCE, timeout=2000)
        frame_vce = page.frame_locator(CSS_IFRAME_VCE)
        frame_vce.locator(f"xpath={XPATH_MODAL_ACEPTAR}").first.evaluate("el => el.click()")
        log("   [ v ] Modal sesion cerrado (Aceptar)")
        page.wait_for_timeout(WAIT_MODAL_CLOSE_MS)
    except Exception:
        pass


def _hay_error_servidor(frame_app):
    """Retorna True si el modal 'Error del Servidor' esta visible en el iframe.
    Deja propagar excepciones que no sean timeout para que el caller sepa si
    el frame esta destruido (no silenciar un frame stale con return False)."""
    try:
        return frame_app.locator(f"xpath={XPATH_ERROR_SERVIDOR}").first.is_visible()
    except PWTimeoutError:
        return False


def _hay_cargando(frame_app):
    """Retorna True si el overlay Cargando... esta visible en el iframe."""
    try:
        return frame_app.locator(f"xpath={XPATH_CARGANDO}").first.is_visible()
    except PWTimeoutError:
        return False


def _cerrar_error_servidor(frame_app, log):
    """Cierra el modal 'Error del Servidor' haciendo click en Aceptar."""
    try:
        frame_app.locator(f"xpath={XPATH_ERROR_SERVIDOR_ACEPTAR}").first.evaluate("el => el.click()")
        log("   [ ! ] Error del Servidor → Aceptar")
        return True
    except Exception:
        return False


def _esperar_sin_cargando(frame_app, timeout_ms=30000):
    """Espera que Cargando... desaparezca. Lanza RefreshNecesario si se queda bloqueado."""
    try:
        loc = frame_app.locator(f"xpath={XPATH_CARGANDO}").first
        if loc.is_visible():
            loc.wait_for(state="hidden", timeout=timeout_ms)
    except PWTimeoutError:
        raise RefreshNecesario("Cargando... bloqueado")
    except Exception:
        pass


def _navegar_por_arbol(page, log):
    """Navega al modulo via arbol de menu sin buscador (usado en recuperacion post-refresh)."""
    _empresas_loc = page.locator(f"xpath={XPATH_EMPRESAS}")
    for _i in range(3):
        try:
            _empresas_loc.first.click(timeout=12000)
            break
        except Exception:
            _dismiss_modal_sesion(page, log)
            if _i == 2:
                raise
    log("   Empresas clickeado")

    for texto in [
        "Comprobantes de pago",
        "Comprobantes de Pago",
        "Consulta de Comprobantes de Pago",
    ]:
        loc = page.locator(
            f"xpath=//*[normalize-space()='{texto}' and not(self::input)]"
        ).first
        loc.wait_for(timeout=15000)
        loc.evaluate("el => el.click()")
        log(f"   > {texto}")
        page.wait_for_timeout(WAIT_MENU_EXPAND_MS)

    nav_loc = page.locator(f"xpath={XPATH_NUEVA_CONSULTA}").first
    nav_loc.wait_for(timeout=15000)
    nav_loc.click()
    log("   > Nueva Consulta de comprobantes de pago")

    page.wait_for_selector(CSS_IFRAME_APP, timeout=40000)
    frame_app = page.frame_locator(CSS_IFRAME_APP)
    log("   Formulario cargado")

    frame_app.locator(f"xpath={XPATH_RUC_EMISOR}").first.wait_for(state="visible", timeout=90000)

    try:
        frame_app.locator(f"xpath={XPATH_FILTRO_RECIBIDO}").first.wait_for(
            state="attached", timeout=15000
        )
        frame_app.locator(f"xpath={XPATH_FILTRO_RECIBIDO}").first.evaluate("el => el.click()")
        log("   Filtro: Recibido")
        page.wait_for_timeout(WAIT_ANGULAR_FILTRO_MS)
    except Exception:
        log("   [ ! ] No se pudo seleccionar Recibido")

    return frame_app


def _recuperar_con_refresh(page, log):
    """Refresca la pagina y re-navega al modulo tras un bloqueo de SUNAT."""
    log("   [ ! ] Refrescando pagina...")
    page.reload(timeout=30000, wait_until="domcontentloaded")
    try:
        page.wait_for_selector(CSS_IFRAME_VCE, timeout=10000)
        frame_vce = page.frame_locator(CSS_IFRAME_VCE)
        log("   Modal post-refresh detectado")
        for i in range(2):
            if not _js_click(XPATH_FINALIZAR, f"Finalizar ({i+1})", page, log, target=frame_vce):
                break
        _js_click(XPATH_CONTINUAR_SIN_CONFIRMAR, "Continuar sin confirmar",
                  page, log, target=frame_vce, timeout=6000)
    except Exception:
        pass
    return _navegar_por_arbol(page, log)


def _recuperar(frame, page=None, log=None):
    """Intenta cerrar dialogo y limpiar formulario tras un error en el frame."""
    if page and log:
        _dismiss_modal_sesion(page, log)
    for xpath in [XPATH_CERRAR, XPATH_LIMPIAR]:
        try:
            frame.locator(f"xpath={xpath}").first.click(timeout=3000)
        except Exception:
            pass
    try:
        frame.locator(f"xpath={XPATH_RUC_EMISOR}").first.wait_for(
            state="visible", timeout=8000
        )
    except Exception:
        pass
