import json
import os
from typing import Any, Callable, Dict, List, TypedDict

import pandas as pd
from playwright.sync_api import sync_playwright

from src.modules.sunat.infrastructure.automation.correo import enviar_agrupado, enviar_individual
from src.modules.sunat.infrastructure.automation.drive import DriveClient, extraer_id

from .selectores import (
    COLUMNAS_REQUERIDAS,
    ES_LOCAL,
    TIPO_CP_MAP,
    DriveTokenExpirado,
    RefreshNecesario,
)
from .navegacion import _recuperar, _recuperar_con_refresh, _wire_debug
from .login import _hacer_login, _navegar_al_modulo
from .descarga import _procesar_comprobante

__all__ = [
    "automatizar",
    "ConfigJob",
    "RefreshNecesario",
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

            # --- Navegar al modulo ---
            try:
                frame_app = _navegar_al_modulo(page, log)
            except Exception as e:
                if cancelar and cancelar.is_set():
                    log("[ ! ] Automatizacion cancelada.")
                else:
                    log(f"[ x ] Error navegando al modulo: {e}")
                return []

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

            # --- Loop de comprobantes ---
            for num_actual, (_, fila) in enumerate(df.iterrows(), start=1):
                if cancelar and cancelar.is_set():
                    log("[ ! ] Automatizacion cancelada.")
                    cancelado = True
                    break

                ruc_emisor = str(fila["Nro Doc Identidad"]).strip()
                serie      = str(fila["Serie del CDP"]).strip()
                numero     = int(fila["Nro CP o Doc. Nro Inicial (Rango)"])
                tipo_num   = int(fila["Tipo CP/Doc."]) if not pd.isna(fila["Tipo CP/Doc."]) else 1
                tipo_texto = TIPO_CP_MAP.get(tipo_num)
                comp_id    = f"{serie}-{numero}"

                # Modo seleccion manual del usuario
                if ids_seleccionados and comp_id not in ids_seleccionados:
                    continue

                # Modo forzar-faltantes: solo procesar los indicados
                if solo_faltantes and comp_id not in solo_faltantes:
                    continue

                flags = solo_faltantes[comp_id] if solo_faltantes else flags_global

                log(f"\n[{num_actual}/{total}] {comp_id} | RUC: {ruc_emisor} | Tipo: {tipo_num}")

                if tipo_texto is None:
                    log(f"   [ x ] Tipo {tipo_num} no esta en TIPO_CP_MAP — omitiendo.")
                    log("         Agrega el texto exacto del dropdown de SUNAT a TIPO_CP_MAP.")
                    resultados.append({"id": comp_id, "pdf": False, "xml": False,
                                       "pide_pdf": flags.get("pdf", True),
                                       "pide_xml": flags.get("xml", True),
                                       "estado": f"Error (tipo {tipo_num})"})
                    procesados += 1
                    prog(int(procesados / total_efectivo * 100))
                    continue

                pide_pdf = flags.get("pdf", True)
                pide_xml = flags.get("xml", True)
                pdf_ok   = False
                xml_ok   = False
                ruta_pdf = None
                ruta_xml = None

                # Flags base sin pdf/xml (preserva contexto y otros)
                flags_base = {k: v for k, v in flags.items() if k not in ("pdf", "xml")}

                # ── Fase PDF (hasta 4 intentos) ──────────────────────────
                if pide_pdf:
                    for intento in range(1, 5):
                        try:
                            desc = _procesar_comprobante(
                                frame_app, page, tipo_texto, ruc_emisor, serie, numero,
                                config, drive_client, drive_folder_id, agrupados, log,
                                flags={**flags_base, "pdf": True, "xml": False, "_solo_descarga": True},
                            )
                            pdf_ok   = desc["pdf"]
                            ruta_pdf = desc["ruta_pdf"]
                            break
                        except RefreshNecesario as e:
                            log(f"   [ ! ] PDF – {e} — requiere refresh")
                            try:
                                frame_app = _recuperar_con_refresh(page, log)
                            except Exception as e2:
                                log(f"   [ x ] Refresh fallido: {str(e2)[:60]}")
                                log("   [ x ] PDF: abortando — no se puede recuperar el frame")
                                break
                            if intento >= 4:
                                log("   [ x ] PDF: no se descargo tras 4 intentos")
                            else:
                                log("   [ . ] Esperando 30s antes de reintentar PDF...")
                                page.wait_for_timeout(30000)
                        except Exception as e:
                            if cancelar and cancelar.is_set():
                                log("[ ! ] Automatizacion cancelada.")
                                cancelado = True
                                break
                            if intento < 4:
                                log(f"   [ ! ] PDF intento {intento} fallido — reintentando...")
                                _recuperar(frame_app, page, log)
                                page.wait_for_timeout(3000)
                            else:
                                log(f"   [ x ] PDF: error tras 4 intentos ({str(e)[:80]})")
                                _recuperar(frame_app, page, log)

                # ── Fase XML (hasta 4 intentos, independiente del PDF) ───
                if pide_xml and not cancelado:
                    for intento in range(1, 5):
                        try:
                            desc = _procesar_comprobante(
                                frame_app, page, tipo_texto, ruc_emisor, serie, numero,
                                config, drive_client, drive_folder_id, agrupados, log,
                                flags={**flags_base, "pdf": False, "xml": True, "_solo_descarga": True},
                            )
                            xml_ok   = desc["xml"]
                            ruta_xml = desc["ruta_xml"]
                            break
                        except RefreshNecesario as e:
                            log(f"   [ ! ] XML – {e} — requiere refresh")
                            try:
                                frame_app = _recuperar_con_refresh(page, log)
                            except Exception as e2:
                                log(f"   [ x ] Refresh fallido: {str(e2)[:60]}")
                                log("   [ x ] XML: abortando — no se puede recuperar el frame")
                                break
                            if intento >= 4:
                                log("   [ x ] XML: no se descargo tras 4 intentos")
                            else:
                                log("   [ . ] Esperando 30s antes de reintentar XML...")
                                page.wait_for_timeout(30000)
                        except Exception as e:
                            if cancelar and cancelar.is_set():
                                log("[ ! ] Automatizacion cancelada.")
                                cancelado = True
                                break
                            if intento < 4:
                                log(f"   [ ! ] XML intento {intento} fallido — reintentando...")
                                _recuperar(frame_app, page, log)
                                page.wait_for_timeout(3000)
                            else:
                                log(f"   [ x ] XML: error tras 4 intentos ({str(e)[:80]})")
                                _recuperar(frame_app, page, log)

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
