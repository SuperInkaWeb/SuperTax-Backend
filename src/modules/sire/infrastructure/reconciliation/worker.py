"""
Procesamiento pesado de la conciliación, pensado para correr en un SUBPROCESO.

Todo el pico de memoria (parsear millones de filas, conciliar, generar los
reportes) vive aquí. Al ejecutarse en un proceso hijo que muere al terminar, el
sistema operativo recupera el 100% de esa memoria — cosa que malloc_trim no
logra por la fragmentación que dejan pandas/numpy.

No usa base de datos ni red: recibe rutas de archivos y datos ya resueltos por
el proceso principal, y devuelve un resumen liviano (conteos y rutas de salida)
que el proceso principal guarda en la BD.
"""

import io

from src.modules.sire.infrastructure.parser.empresa_file import (
    parse_empresa_file, KNOWN_FORMAT_COLUMNS_HELP, PLE81_FORMAT_HELP,
)
from src.modules.sire.infrastructure.parser.mapeo import parse_con_columnas, validar_mapeo
from src.modules.sire.infrastructure.parser.sunat_propuesta import parse_sunat_propuesta
from src.modules.sire.infrastructure.reconciliation.engine import reconcile
from src.modules.sire.infrastructure.report.excel_generator import (
    generate_excel, generate_csv_a, generate_csv_b, generate_csv_c, generate_csv_d,
    EXCEL_MAX_DATA_ROWS,
)
from src.platform.storage import storage


def _parsear_empresa(contenido: bytes, empresa_filename: str, tipo_libro: str,
                     mapeo_config: dict | None, saved_mapping: dict | None) -> list:
    """
    Convierte el archivo de la empresa en registros según el mapeo aplicable
    (explícito, formato conocido o guardado). Réplica sin BD de la resolución
    que antes hacía el proceso principal.
    """
    def _parse_con(config: dict) -> list:
        val = validar_mapeo(contenido, config, tipo_libro)
        if not val["ok"]:
            detalle = "; ".join(val["avisos"]) if val["avisos"] else f"faltan campos: {val['faltantes']}"
            raise ValueError(f"El mapeo de columnas no supera la validación: {detalle}")
        recs = parse_con_columnas(contenido, config, tipo_libro)
        if not recs:
            raise ValueError("No se extrajo ningún registro con el mapeo de columnas configurado.")
        return recs

    if mapeo_config is not None:
        return _parse_con(mapeo_config)

    registros, used_mapping = parse_empresa_file(contenido, empresa_filename, None, tipo_libro)
    if used_mapping.known_format:
        return registros
    if saved_mapping is not None:
        return _parse_con(saved_mapping)

    formato_esperado = (
        PLE81_FORMAT_HELP if tipo_libro == "compras"
        else f"CSV con las columnas: {KNOWN_FORMAT_COLUMNS_HELP}"
    )
    detalle = "; ".join(used_mapping.warnings) or "formato no reconocido"
    raise ValueError(
        f"El archivo no tiene el formato esperado para {tipo_libro}. "
        f"{detalle}. Formato esperado: {formato_esperado}."
    )


def _meses_anteriores(periodo: str, n: int) -> set[str]:
    """Los n periodos AAAAMM inmediatamente anteriores a `periodo`."""
    y, m = int(periodo[:4]), int(periodo[4:6])
    out: set[str] = set()
    for _ in range(n):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        out.add(f"{y:04d}{m:02d}")
    return out


def extraer_periodos_emision(payload: dict) -> list[str]:
    """
    Sondeo para compras "sin SIRE" (corre en un subproceso propio, así la RAM
    del parseo se libera al terminar): devuelve los periodos AAAAMM distintos de
    las fechas de emisión del archivo de la empresa que caen en los 12 meses
    anteriores al periodo conciliado (excluye el propio periodo). Son las
    propuestas que habrá que descargar para reubicar los comprobantes rezagados.
    """
    periodo = payload["periodo"]
    contenido = storage.read(payload["empresa_file_path"])
    empresa_records = _parsear_empresa(
        contenido, payload["empresa_filename"], payload["tipo_libro"],
        payload["mapeo_config"], payload["saved_mapping"],
    )
    del contenido

    candidatos = _meses_anteriores(periodo, 12)
    meses: set[str] = set()
    for r in empresa_records:
        f = r.fecha_emision
        if f and len(f) >= 7 and f[:4].isdigit() and f[5:7].isdigit():
            mes = f[:4] + f[5:7]
            if mes in candidatos:
                meses.add(mes)
    return sorted(meses)


def procesar_conciliacion(payload: dict) -> dict:
    """
    Punto de entrada del subproceso. Recibe un payload plano (picklable) y
    devuelve un resumen liviano. Guarda el Excel/CSV en storage por su cuenta.
    """
    tipo_libro = payload["tipo_libro"]
    company_id = payload["company_id"]
    job_id = payload["job_id"]
    ruc = payload["ruc"]
    periodo = payload["periodo"]

    contenido = storage.read(payload["empresa_file_path"])
    empresa_records = _parsear_empresa(
        contenido, payload["empresa_filename"], tipo_libro,
        payload["mapeo_config"], payload["saved_mapping"],
    )
    del contenido

    with open(payload["sunat_tmp_path"], "rb") as f:
        sunat_bytes = f.read()
    sunat_records = parse_sunat_propuesta(sunat_bytes, tipo_libro)
    del sunat_bytes

    # Compras "sin SIRE": propuestas de meses anteriores para reubicar los
    # comprobantes rezagados (cada una es la ruta a un TXT ya descargado).
    sunat_extra = None
    extra_paths = payload.get("sunat_extra_paths") or {}
    if extra_paths:
        sunat_extra = {}
        for periodo_extra, ruta in extra_paths.items():
            with open(ruta, "rb") as f:
                sunat_extra[periodo_extra] = parse_sunat_propuesta(f.read(), tipo_libro)

    cobertura = set(payload["cobertura_fechas"]) if payload["cobertura_fechas"] is not None else None
    recon_output = reconcile(
        empresa_records, sunat_records, tipo_libro, cobertura,
        sunat_extra=sunat_extra, periodo=payload["periodo"],
    )
    del empresa_records, sunat_records

    excel_bytes = generate_excel(
        output=recon_output,
        empresa_nombre=payload["empresa_nombre"],
        ruc=ruc,
        periodo=periodo,
        tipo_libro=tipo_libro,
        propuesta_generada=payload["propuesta_origen_at"],
        cobertura=payload["cobertura_desc"],
        sin_sire=bool(payload.get("sin_sire")),
        meses_no_disponibles=payload.get("sunat_extra_fallidos"),
    )
    filename_xlsx = f"{ruc}_{periodo}_{tipo_libro}.xlsx"
    path_xlsx = f"reportes/{company_id}/{job_id}/{filename_xlsx}"
    storage.save(path_xlsx, excel_bytes)
    excel_size = len(excel_bytes)
    del excel_bytes

    # Cada escenario que supera el máximo de filas de Excel se vuelca completo a
    # un CSV descargable (los que caben en la hoja no generan CSV).
    csv_out: dict[str, tuple] = {}
    for etiqueta, escenario, gen in (
        ("A", recon_output.scenario_a, generate_csv_a),
        ("B", recon_output.scenario_b, generate_csv_b),
        ("C", recon_output.scenario_c, generate_csv_c),
        ("D", recon_output.scenario_d, generate_csv_d),
    ):
        if len(escenario) > EXCEL_MAX_DATA_ROWS:
            ruta = f"reportes/{company_id}/{job_id}/{ruc}_{periodo}_{tipo_libro}_{etiqueta}.csv"
            # Streaming a disco: se escribe fila por fila, sin armar el CSV
            # entero (millones de filas) en RAM. Salida byte-idéntica a la previa.
            with storage.open_write(ruta) as fb:
                ft = io.TextIOWrapper(fb, encoding="utf-8-sig", newline="")
                gen(recon_output, tipo_libro, ft)
                ft.flush()
                ft.detach()  # libera fb para que open_write lo cierre (fsync + fadvise)
            csv_out[etiqueta] = (ruta, storage.size(ruta))
        else:
            csv_out[etiqueta] = (None, None)

    return {
        "escenario_a_count": len(recon_output.scenario_a),
        "escenario_b_count": len(recon_output.scenario_b),
        "escenario_c_count": len(recon_output.scenario_c),
        "escenario_d_count": len(recon_output.scenario_d),
        "igv_diferencia_total": recon_output.igv_diferencia_total,
        "tiene_alertas_rojas": recon_output.tiene_alertas_rojas,
        "filename_xlsx": filename_xlsx,
        "path_xlsx": path_xlsx,
        "excel_size": excel_size,
        "path_csv_a": csv_out["A"][0], "csv_a_size": csv_out["A"][1],
        "path_csv":   csv_out["B"][0], "csv_b_size": csv_out["B"][1],
        "path_csv_c": csv_out["C"][0], "csv_c_size": csv_out["C"][1],
        "path_csv_d": csv_out["D"][0], "csv_d_size": csv_out["D"][1],
    }
