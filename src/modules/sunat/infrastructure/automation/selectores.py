import os
import sys

ES_LOCAL = sys.platform == "win32"
DEBUG    = os.environ.get("SUNAT_DEBUG", "").lower() in ("1", "true", "yes")

TIPO_CP_MAP = {
    1:  "Factura",
    2:  "Recibo por Honorarios",
    3:  "Boleta de Venta",
    7:  "Factura - Nota de Credito",
    8:  "Factura - Nota de Debito",
    20: "Comprobante de Retencion",
    40: "Liquidacion de Compra",
}

COLUMNAS_REQUERIDAS = [
    "Nro Doc Identidad",
    "Tipo CP/Doc.",
    "Serie del CDP",
    "Nro CP o Doc. Nro Inicial (Rango)",
]

# ── CSS selectors ────────────────────────────────────────────────────────────
CSS_IFRAME_APP = "iframe#iframeApplication"
CSS_IFRAME_VCE = "iframe#ifrVCE"

# ── XPath — acciones de formulario ───────────────────────────────────────────
XPATH_CONSULTAR = "//button[@type='submit']"
XPATH_CERRAR    = "//button[@aria-label='Close' or contains(@class,'close-without-header')]"
XPATH_LIMPIAR   = "//button[contains(normalize-space(),'Limpiar')]"
XPATH_FINALIZAR = (
    "//button[contains(., 'Finalizar')] | "
    "//input[contains(@value,'Finalizar')] | "
    "//a[contains(., 'Finalizar')]"
)

# ── XPath — descarga ─────────────────────────────────────────────────────────
XPATH_PDF = "//button[@ngbtooltip='Descargar PDF']"
XPATH_XML = "//button[@ngbtooltip='Descargar XML']"

# ── XPath — formulario de busqueda ───────────────────────────────────────────
XPATH_RUC_EMISOR    = "//input[@name='rucEmisor']"
XPATH_SERIE         = "//input[@name='serieComprobante']"
XPATH_NUMERO        = "//input[@name='numeroComprobante']"
XPATH_TIPO_DROPDOWN = (
    "//p-dropdown[@formcontrolname='tipoComprobanteI']"
    "//span[contains(@class,'p-dropdown-label')]"
)
XPATH_TIPO_FILTRO = "//input[contains(@class,'p-dropdown-filter')]"
XPATH_TIPO_ITEM   = "//li[contains(@class,'p-dropdown-item') and @aria-label='{tipo}']"

# ── XPath — estados de pagina ────────────────────────────────────────────────
XPATH_ERROR_SERVIDOR = "//*[contains(normalize-space(),'Error del Servidor')]"
XPATH_ERROR_SERVIDOR_ACEPTAR = (
    "//*[contains(normalize-space(),'Error del Servidor')]"
    "/following::button[normalize-space()='Aceptar'][1]"
)
XPATH_CARGANDO = "//*[normalize-space()='Cargando...']"

# ── XPath — navegacion al modulo ─────────────────────────────────────────────
XPATH_EMPRESAS = (
    "//*[contains(@id,'divOpcionServicio') and contains(normalize-space(),'Empresas')] | "
    "//a[normalize-space()='Empresas'] | //span[normalize-space()='Empresas'] | "
    "//li[normalize-space()='Empresas']"
)
XPATH_BUSCADOR = "//input[@id='inputBuscador' or contains(@placeholder,'Busque')]"
XPATH_NUEVA_CONSULTA = (
    "//span[contains(@class,'spanNivelDescripcion') and "
    "contains(normalize-space(),'Nueva Consulta de comprobantes')]"
)
XPATH_FILTRO_RECIBIDO = "//input[@id='recibido']"

# ── XPath — modales ──────────────────────────────────────────────────────────
XPATH_MODAL_ACEPTAR = (
    "//button[contains(normalize-space(),'Aceptar')] | "
    "//input[@value='Aceptar'] | "
    "//a[contains(normalize-space(),'Aceptar')]"
)
XPATH_CONTINUAR_SIN_CONFIRMAR = (
    "//button[contains(.,'Continuar sin confirmar')] | "
    "//a[contains(.,'Continuar sin confirmar')]"
)


# ── Tiempos de espera (ms) ───────────────────────────────────────────────────
WAIT_JS_CLICK_MS       = 300   # post JS click, para que el DOM reaccione
WAIT_MODAL_CLOSE_MS    = 500   # tras cerrar un modal
WAIT_MENU_EXPAND_MS    = 800   # entre clics del arbol de menu (animacion de expansion)
WAIT_ANGULAR_FILTRO_MS = 2000  # tras cambiar radio button, Angular actualiza el formulario
WAIT_PDF_XML_MS        = 800   # pausa entre descarga PDF y XML
WAIT_LIMPIAR_MS        = 1500  # tras Limpiar, Angular resetea el formulario


# ── Excepciones ──────────────────────────────────────────────────────────────
class RefreshNecesario(Exception):
    """SUNAT quedo bloqueado — se necesita refrescar la pagina y re-navegar."""
    pass


class DriveTokenExpirado(Exception):
    """El token de Google Drive expiro o fue revocado."""
    pass
