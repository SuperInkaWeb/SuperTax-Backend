import json
import os
from typing import Any, Callable, Dict, List, TypedDict

import pandas as pd
from playwright.sync_api import sync_playwright

from src.modules.sunat.infrastructure.automation.correo import enviar_agrupado, enviar_individual
from src.modules.sunat.infrastructure.automation.drive import DriveClient, extraer_id

from .consultacpe import TokenExpirado, descargar_archivo
from .login import _hacer_login, _navegar_al_modulo
from .navegacion import _wire_debug
from .selectores import COLUMNAS_REQUERIDAS, ES_LOCAL, DriveTokenExpirado
from .token import capturar_tokens, extraer_token

__all__ = [
    "automatizar",
    "ConfigJob",
    "DriveTokenExpirado",
]


class ConfigJob(TypedDict, total=False):
    """Contrato de tipos para el dict de configuracion que recibe automatizar()."""
    ruc: str
    usuario: str
    clave: str
    usar_correo: bool
    gmail_user: str
    gmail_pass: str
    destino: str
    modo_correo: str
    usar_drive: bool
    drive_folder: str
    drive_access_token: str
    drive_refresh_token: str
    excel: str
    descargas: str
    _cancelar: Any
    _persist_drive_token: Callable
    descargar_pdf: bool
    descargar_xml: bool
    comprobantes_seleccionados: List
    solo_faltantes: List


def automatizar(config: "ConfigJob", log_q, prog_q) -> List[Dict]:
    """Orquesta login, navegacion y descarga de todos los comprobantes del Excel."""
    def log(msg):  log_q.put(msg)
    def prog(val): prog_q.put(val)

    # --- Validar y cargar Excel ---
    try:
        df = pd.read_excel(config["excel"])
        faltantes = [c for c in COLUMNAS_REQUERIDAS if c not in df.columns]
        if faltantes:
            log(f"[ x ] El Excel no tiene las columnas requeridas: {faltantes}")
            log(f"      Columnas encontradas: {list(df.columns)}")
            return []
        df = df[COLUMNAS_REQUERIDAS].dropna(subset=["Serie del CDP"])
        df["Nro Doc Identidad"] = df["Nro Doc Identidad"].astype(str).str.strip()
        total = len(df)
        if total == 0:
            log("[ x ] El Excel no tiene filas validas (todas tienen 'Serie del CDP' vacia)")
            return []
        log(f"[ i ] {total} comprobantes cargados del Excel")
    except Exception as e:
        log(f"[ x ] Error leyendo Excel: {e}")
        return []

    os.makedirs(config["descargas"], exist_ok=True)
    cancelar = config.get("_cancelar")

    with sync_playwright() as p:
        # --- Lanzar navegador ---
        try:
            args_base = [
                "--disable-blink-features=AutomationControlled",
                "--window-size=1366,768",
                "--disable-extensions",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-translate",
            ]
            args_linux = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-zygote",
                "--disable-background-networking",
            ]
            browser = p.chromium.launch(
                headless=not ES_LOCAL,
                args=args_base + ([] if ES_LOCAL else args_linux),
            )
            context = browser.new_context(
                accept_downloads=True,
                viewport={"width": 1366, "height": 768},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                " window.chrome = {runtime: {}};"
            )
            _wire_debug(page, log)
            # Interceptor para tomar el Bearer token de la sesión (para consultacpe).
            capturados = capturar_tokens(page)
        except Exception as e:
            log(f"[ x ] No se pudo abrir el navegador: {e}")
            return []

        # Nota: cancelar se revisa en los puntos de control del bucle.
        # NO se cierra el browser desde otro hilo — Playwright sync no es
        # thread-safe. El cierre lo hace el finally de abajo.

        resultados = []
        agrupados  = []
        cancelado  = False

        try:
            # --- Login ---
            try:
                page = _hacer_login(page, context, config, log)
            except Exception as e:
                if cancelar and cancelar.is_set():
                    log("[ ! ] Automatizacion cancelada.")
                else:
                    log(f"[ x ] Error en el login SUNAT: {e}")
                return []

            # --- Navegar al modulo (dispara la carga del token en el SPA) ---
            try:
                _navegar_al_modulo(page, log)
            except Exception as e:
                if cancelar and cancelar.is_set():
                    log("[ ! ] Automatizacion cancelada.")
                else:
                    log(f"[ x ] Error navegando al modulo: {e}")
                return []

            # --- Token de la sesión (se descarga por HTTP con consultacpe) ---
            token = extraer_token(page, capturados)
            if not token:
                log("[ x ] No se pudo obtener el token de la sesion SUNAT.")
                return []
            log("   [ v ] Token de sesion obtenido")

            def _refrescar_token():
                """Re-navega para que el SPA emita un token nuevo y lo re-extrae."""
                try:
                    _navegar_al_modulo(page, log)
                    page.wait_for_timeout(1500)
                    return extraer_token(page, capturados)
                except Exception as exc:
                    log(f"   [ x ] No se pudo refrescar el token: {str(exc)[:80]}")
                    return None

            def _bajar(tipo_archivo, ruc_e, tipo_cod, serie_c, numero_c):
                """Descarga un archivo; si el token expiró (401), lo refresca 1 vez."""
                nonlocal token
                try:
                    return descargar_archivo(token, ruc_e, tipo_cod, serie_c, numero_c, tipo_archivo)
                except TokenExpirado:
                    log("   [ ! ] Token de sesion expirado — refrescando...")
                    nuevo = _refrescar_token()
                    if not nuevo:
                        raise
                    token = nuevo
                    return descargar_archivo(token, ruc_e, tipo_cod, serie_c, numero_c, tipo_archivo)

            # --- Inicializar Drive (una sola vez por job) ---
            drive_client    = None
            drive_folder_id = ""
            if config.get("usar_drive") and config.get("drive_folder") and config.get("drive_access_token"):
                try:
                    drive_client = DriveClient(
                        config["drive_access_token"],
                        config.get("drive_refresh_token", ""),
                        on_refresh=config.get("_persist_drive_token"),
                    )
                    drive_folder_id = extraer_id(config["drive_folder"])
                    log("   [ v ] Google Drive listo")
                except Exception as e:
                    log(f"   [ ! ] No se pudo inicializar Google Drive: {e}")

            # --- Flags globales de descarga y modo forzar-faltantes ---
            flags_global = {
                "pdf":      config.get("descargar_pdf", True),
                "xml":      config.get("descargar_xml", True),
                "contexto": "filtro",
            }
            _sf = config.get("solo_faltantes", [])
            solo_faltantes = {
                item["id"]: {
                    "pdf":      item.get("descargar_pdf", True),
                    "xml":      item.get("descargar_xml", True),
                    "contexto": "forzar",
                }
                for item in _sf
            }
            ids_seleccionados = set(config.get("comprobantes_seleccionados", []))
            total_efectivo = (
                len(solo_faltantes) if solo_faltantes else
                len(ids_seleccionados) if ids_seleccionados else
                total
            )
            procesados = 0

            # --- Loop de comprobantes (descarga por HTTP: consultacpe) ---
            for num_actual, (_, fila) in enumerate(df.iterrows(), start=1):
                if cancelar and cancelar.is_set():
                    log("[ ! ] Automatizacion cancelada.")
                    cancelado = True
                    break

                ruc_emisor = str(fila["Nro Doc Identidad"]).strip()
                serie      = str(fila["Serie del CDP"]).strip()
                numero     = int(fila["Nro CP o Doc. Nro Inicial (Rango)"])
                tipo_num   = int(fila["Tipo CP/Doc."]) if not pd.isna(fila["Tipo CP/Doc."]) else 1
                tipo_cod   = f"{tipo_num:02d}"  # 1->01, 3->03, 7->07, 8->08 (código SUNAT)
                comp_id    = f"{serie}-{numero}"

                # Modo seleccion manual del usuario
                if ids_seleccionados and comp_id not in ids_seleccionados:
                    continue

                # Modo forzar-faltantes: solo procesar los indicados
                if solo_faltantes and comp_id not in solo_faltantes:
                    continue

                flags = solo_faltantes[comp_id] if solo_faltantes else flags_global
                pide_pdf = flags.get("pdf", True)
                pide_xml = flags.get("xml", True)
                pdf_ok   = False
                xml_ok   = False
                ruta_pdf = None
                ruta_xml = None

                log(f"\n[{num_actual}/{total}] {comp_id} | RUC: {ruc_emisor} | Tipo: {tipo_num}")

                try:
                    if pide_xml:
                        res = _bajar("xml", ruc_emisor, tipo_cod, serie, numero)
                        if res:
                            nombre, contenido = res
                            ruta_xml = os.path.join(config["descargas"], nombre)
                            with open(ruta_xml, "wb") as fx:
                                fx.write(contenido)
                            xml_ok = True
                            log(f"   [ v ] XML: {nombre}")
                        else:
                            log("   [ - ] XML no disponible")
                    if pide_pdf:
                        res = _bajar("pdf", ruc_emisor, tipo_cod, serie, numero)
                        if res:
                            nombre, contenido = res
                            ruta_pdf = os.path.join(config["descargas"], nombre)
                            with open(ruta_pdf, "wb") as fp:
                                fp.write(contenido)
                            pdf_ok = True
                            log(f"   [ v ] PDF: {nombre}")
                        else:
                            log("   [ - ] PDF no disponible")
                except TokenExpirado:
                    log("[ x ] La sesion SUNAT expiro y no se pudo refrescar. Deteniendo.")
                    cancelado = True

                # ── Drive y correo (con lo que se haya descargado) ───────
                if not cancelado:
                    try:
                        if drive_client and (ruta_pdf or ruta_xml):
                            if ruta_pdf:
                                drive_client.subir_archivo(drive_folder_id, ruta_pdf)
                            if ruta_xml:
                                drive_client.subir_archivo(drive_folder_id, ruta_xml)
                            log("   [ v ] Subido a Google Drive")
                        if (config.get("usar_correo") and config["gmail_user"]
                                and config["gmail_pass"] and config["destino"]
                                and (ruta_pdf or ruta_xml)):
                            if config["modo_correo"] == "individual":
                                enviar_individual(config, serie, numero, ruta_xml, ruta_pdf)
                                log(f"   [ v ] Correo enviado a {config['destino']}")
                            else:
                                agrupados.append((serie, numero, ruta_xml, ruta_pdf))
                                log("   [ + ] Archivos acumulados para envio final")
                    except DriveTokenExpirado:
                        log("[ x ] Google Drive: token expirado o revocado.")
                        log("      Reconecta tu cuenta de Google desde la app y vuelve a intentar.")
                        cancelado = True
                    except Exception as e:
                        if "invalid_grant" in str(e):
                            log("[ x ] Google Drive: token expirado o revocado.")
                            log("      Reconecta tu cuenta de Google desde la app y vuelve a intentar.")
                            cancelado = True
                        else:
                            log(f"   [ ! ] Error en Drive/correo: {e}")

                # ── Resultado ─────────────────────────────────────────────
                pdf_fallo = pide_pdf and not pdf_ok
                xml_fallo = pide_xml and not xml_ok
                if not pdf_fallo and not xml_fallo:
                    estado = "OK"
                elif pdf_ok or xml_ok:
                    estado = "Parcial"
                else:
                    estado = "Error"
                resultados.append({
                    "id": comp_id, "pdf": pdf_ok, "xml": xml_ok,
                    "pide_pdf": pide_pdf, "pide_xml": pide_xml,
                    "estado": estado,
                })

                if cancelado:
                    break

                procesados += 1
                prog(int(procesados / total_efectivo * 100))

        finally:
            try:
                browser.close()
            except Exception:
                pass

    # --- Correo agrupado al final ---
    if (agrupados and config.get("usar_correo")
            and config["gmail_user"] and config["gmail_pass"] and config["destino"]):
        try:
            enviar_agrupado(config, agrupados)
            log(f"\n[ v ] Correo agrupado enviado con {len(agrupados)} comprobantes")
        except Exception as e:
            log(f"\n[ x ] Error enviando correo agrupado: {e}")

    # --- Resumen final ---
    ok      = sum(1 for r in resultados if r["estado"] == "OK")
    parcial = sum(1 for r in resultados if r["estado"] == "Parcial")
    err     = sum(1 for r in resultados if r["estado"] not in ("OK", "Parcial"))
    log("\n" + "=" * 45)
    log("  RESUMEN FINAL")
    log("=" * 45)
    for r in resultados:
        icon  = "v" if r["estado"] == "OK" else ("~" if r["estado"] == "Parcial" else "x")
        pdf_s = "PDF:si" if r["pdf"] else "PDF:no"
        xml_s = "XML:si" if r["xml"] else "XML:no"
        log(f"  [ {icon} ]  {r['id']}  {pdf_s}  {xml_s}")
    log(f"\n  OK: {ok}  Parcial: {parcial}  Error: {err}")
    log("=" * 45)
    log(f"__RESULTADOS__:{json.dumps(resultados)}")
    prog(100)
    return resultados
