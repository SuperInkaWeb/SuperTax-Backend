from .selectores import (
    CSS_IFRAME_VCE,
    DEBUG,
    WAIT_JS_CLICK_MS,
    WAIT_MODAL_CLOSE_MS,
    XPATH_MODAL_ACEPTAR,
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
