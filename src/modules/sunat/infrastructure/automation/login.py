import urllib.parse

from playwright.sync_api import TimeoutError as PWTimeoutError

from .selectores import (
    CSS_IFRAME_APP,
    CSS_IFRAME_VCE,
    DEBUG,
    WAIT_ANGULAR_FILTRO_MS,
    XPATH_FILTRO_RECIBIDO,
    XPATH_FINALIZAR,
    XPATH_CONTINUAR_SIN_CONFIRMAR,
    XPATH_EMPRESAS,
    XPATH_BUSCADOR,
    XPATH_RUC_EMISOR,
)
from .navegacion import _wire_debug, _js_click, _dismiss_modal_sesion


def _hacer_login(page, context, config, log):
    """Navega al portal SUNAT, llena credenciales y resuelve el flujo OAuth.

    Devuelve la pagina activa tras el login (puede ser una popup distinta
    a la entrada). Lanza Exception si no se puede completar el login.
    """
    log("Abriendo portal SUNAT (Mis tramites y consultas)...")
    # Entrada directa al menu SOL. Hace un redirect JS al login de api-seguridad
    # con el cliente OAuth 4f3b88b3, que redirige bien desde IPs de datacenter
    # (a diferencia del flujo loginMenuSol/cliente 00000003).
    page.goto(
        "https://e-menu.sunat.gob.pe/cl-ti-itmenucabina/MenuInternet.htm",
        timeout=60000,
        wait_until="domcontentloaded",
    )

    log("Llenando credenciales...")
    page.wait_for_selector("#txtRuc", timeout=60000)
    # Guardamos el 'state' del login (rO0ABX...) porque SUNAT a veces lo pierde
    # al redirigir y el handler del menu lo exige en la redireccion forzada.
    login_qs    = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
    state_login = (login_qs.get("state") or [""])[0]
    page.fill("#txtRuc", config["ruc"])
    page.fill("#txtUsuario", config["usuario"])
    page.fill("#txtContrasena", config["clave"])

    # Tras Aceptar el menu puede abrirse en la misma pestana o en una nueva.
    try:
        with context.expect_page(timeout=5000) as popup_info:
            page.click("#btnAceptar")
        page = popup_info.value
        page.wait_for_load_state("domcontentloaded", timeout=60000)
        _wire_debug(page, log)
        log("   Menu abierto en pestana nueva")
    except PWTimeoutError:
        log("   Menu en la misma pestana")

    # Esperar a que el redirect OAuth termine: o llega al menu (no hay
    # "api-seguridad" en la URL) o se queda con ?code= (OAuth emitio el
    # codigo pero el JS no navego de vuelta). 30 s es suficiente en la nube.
    try:
        page.wait_for_url(
            lambda url: "api-seguridad" not in url or "code=" in url,
            timeout=30000,
        )
    except PWTimeoutError:
        pass
    log(f"   URL post-login: {page.url}")

    # OAuth colgado: el login emitio el code pero la pagina no navego al menu.
    # Forzamos el intercambio navegando al handler con las cookies ya en el contexto.
    if "api-seguridad.sunat.gob.pe" in page.url:
        # Si el formulario de login sigue visible, nunca se autenticó — reintentar
        try:
            form_visible = page.locator("#txtRuc").is_visible(timeout=1000)
        except Exception:
            form_visible = False

        if form_visible:
            log("   [ ! ] Formulario pegado — reintentando Iniciar sesion...")
            page.fill("#txtRuc", config["ruc"])
            page.fill("#txtUsuario", config["usuario"])
            page.fill("#txtContrasena", config["clave"])
            page.locator("#btnAceptar").evaluate("el => el.click()")
            try:
                page.wait_for_url(
                    lambda url: "api-seguridad" not in url or "code=" in url,
                    timeout=30000,
                )
            except PWTimeoutError:
                pass
            log(f"   URL tras reintento login: {page.url}")

    if "api-seguridad.sunat.gob.pe" in page.url:
        log("   [ ! ] Sesion colgada en api-seguridad; forzando entrada al menu...")
        qs    = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
        code  = (qs.get("code")  or [""])[0]
        state = (qs.get("state") or [""])[0] or state_login
        destinos = []
        if code:
            destinos.append(
                "https://e-menu.sunat.gob.pe/cl-ti-itmenu/AutenticaMenuInternet.htm"
                "?code=" + urllib.parse.quote(code) + "&state=" + urllib.parse.quote(state)
            )
        destinos.append("https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm")
        for destino in destinos:
            try:
                page.goto(destino, timeout=60000, wait_until="domcontentloaded")
                try:
                    page.wait_for_url(
                        lambda url: "api-seguridad" not in url,
                        timeout=15000,
                    )
                except PWTimeoutError:
                    pass
                log(f"   URL tras forzar: {page.url}")
                if "api-seguridad" not in page.url:
                    break
            except Exception as e:
                log(f"   [ ! ] Forzar fallo: {str(e)[:150]}")

    # Modal post-login (ifrVCE): aparece a veces con pasos de confirmacion.
    try:
        page.wait_for_selector(CSS_IFRAME_VCE, timeout=10000)
        frame_vce = page.frame_locator(CSS_IFRAME_VCE)
        log("   Modal post-login detectado")
        for i in range(2):
            if not _js_click(XPATH_FINALIZAR, f"Finalizar ({i+1})", page, log, target=frame_vce):
                break
        _js_click(XPATH_CONTINUAR_SIN_CONFIRMAR, "Continuar sin confirmar",
                  page, log, target=frame_vce, timeout=6000)
    except Exception:
        log("   Sin modal post-login")

    if DEBUG:
        try:
            log(f"   [page] url={page.url}")
            log(f"   [page] title={page.title()!r}")
            cuerpo = page.inner_text("body")[:700].replace(chr(10), " | ")
            log(f"   [page] texto: {cuerpo}")
        except Exception as e:
            log(f"   [page] no se pudo leer la pagina: {e}")

    log("Sesion iniciada")
    return page


def _navegar_al_modulo(page, log):
    """Desde el menu principal navega a 'Nueva Consulta de comprobantes'.

    Devuelve el frame_locator del iframe de la aplicacion listo para usar.
    Lanza Exception si no puede llegar al formulario.
    """
    log("Navegando al modulo de consulta...")

    # El modal de sesion SUNAT puede aparecer en cualquier momento y bloquear clicks.
    # Intentamos hasta 3 veces con timeout corto; si falla, descartamos el modal y reintentamos.
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

    buscador = page.locator(f"xpath={XPATH_BUSCADOR}").first
    buscador.wait_for(timeout=40000)
    buscador.click()
    buscador.fill("Nueva Consulta de comprobantes")
    log("   Buscador llenado")

    # Esperar a que aparezcan los resultados antes de leerlos
    page.locator(
        "xpath=//*[contains(normalize-space(),'Nueva Consulta de comprobantes') and not(self::input)]"
    ).first.wait_for(timeout=15000)

    candidatos = page.locator(
        "xpath=//*[contains(normalize-space(),'Nueva Consulta de comprobantes') and not(self::input)]"
    ).all()
    resultado_link = min(
        (el for el in candidatos if "Nueva Consulta" in (el.text_content() or "")),
        key=lambda el: len(el.text_content() or ""),
        default=None,
    )
    if not resultado_link:
        raise Exception("No se encontro 'Nueva Consulta de comprobantes' en el buscador")

    resultado_link.evaluate("el => el.click()")
    log("   Nueva Consulta seleccionada")

    page.wait_for_selector(CSS_IFRAME_APP, timeout=40000)
    frame_app = page.frame_locator(CSS_IFRAME_APP)
    log("   Formulario cargado")

    # Esperar que el formulario Angular este completamente listo antes de
    # aplicar filtros. En Cloud Run (headless) el iframe tarda mas en inicializar.
    frame_app.locator(f"xpath={XPATH_RUC_EMISOR}").first.wait_for(
        state="visible", timeout=90000
    )

    # Activar filtro Recibido ahora que el formulario esta listo
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
