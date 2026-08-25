"""
asistencia.py — Extrae registros de planillas de asistencia laborales.

Formatos soportados:
  1. Excel (.xlsx/.xls) — vertical o horizontal (días como columnas)
  2. PDF escaneado / imagen — lectura con IA de visión (Groq)
  3. PDF con texto embebido — parsing de layout tabular

Campos por registro:
  trabajador, dni, cargo, departamento, empresa, periodo,
  fecha, hora_entrada, hora_salida, turno,
  horas_normales, horas_extras, firma_presente, dia_libre
"""
import os
import re
import tempfile

from src.modules.scanner.infrastructure.utils import (
    EXTENSIONES_IMAGEN,
    limpiar_texto,
    normalizar_fecha,
    normalizar_hora,
    pdf_a_texto,
)

EXTENSIONES_EXCEL = {".xlsx", ".xls"}


# ── Dispatcher principal ──────────────────────────────────────────────────────

def extract_asistencia(file_path: str) -> list:
    ext = os.path.splitext(file_path)[1].lower()

    if ext in EXTENSIONES_EXCEL:
        return _desde_excel(file_path)

    if ext in EXTENSIONES_IMAGEN:
        return _desde_imagen(file_path)

    # PDF: decidir si tiene texto o es escaneado
    import fitz
    doc = fitz.open(file_path)
    paginas = list(doc)
    tiene_texto = any(p.get_text().strip() for p in paginas)
    doc.close()

    if tiene_texto:
        return _desde_texto(limpiar_texto(pdf_a_texto(file_path)))
    else:
        import fitz as fitz2
        doc2 = fitz2.open(file_path)
        # Renderizar todas las páginas
        imgs = []
        for i, page in enumerate(doc2):
            mat = fitz2.Matrix(4, 4)
            pix = page.get_pixmap(matrix=mat)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=tempfile.gettempdir()) as tmp_f:
                tmp = tmp_f.name
            pix.save(tmp)
            imgs.append(tmp)
        doc2.close()

        # Procesar páginas y consolidar contexto de encabezado entre ellas
        todas = []
        ctx = {}  # contexto compartido
        for tmp in imgs:
            regs = _desde_imagen(tmp, ctx_anterior=ctx)
            # Actualizar contexto con lo que se haya encontrado en esta página
            for r in regs:
                if not ctx.get("trabajador") and r.get("trabajador"):
                    ctx["trabajador"] = r["trabajador"]
                if not ctx.get("dni") and r.get("dni"):
                    ctx["dni"] = r["dni"]
                if not ctx.get("empresa") and r.get("empresa"):
                    ctx["empresa"] = r["empresa"]
                if not ctx.get("anio") and r.get("fecha"):
                    try:
                        ctx["anio"] = r["fecha"].split("-")[0]
                        ctx["mes"]  = r["fecha"].split("-")[1]
                    except Exception:
                        pass
                if not ctx.get("anio") and r.get("periodo"):
                    # Intentar extraer año del periodo
                    m_yr = re.search(r"(20\d{2})", r["periodo"])
                    if m_yr:
                        ctx["anio"] = m_yr.group(1)
            todas += regs
            if os.path.exists(tmp):
                os.remove(tmp)
        return todas


# ===========================================================================
# IMAGEN ESCANEADA / FOTO — IA de visión (Groq)
# ===========================================================================

_PROMPT_ASISTENCIA = """Eres un asistente que lee planillas de control de asistencia
laboral peruanas a partir de una foto o escaneo.

Lee la tabla y devuelve ÚNICAMENTE un JSON válido con esta estructura exacta:
{
  "trabajador": "nombre completo del trabajador",
  "dni": "8 dígitos",
  "cargo": "cargo o puesto",
  "empresa": "razón social",
  "periodo": "MES AÑO (ej. ENERO 2024)",
  "registros": [
    {
      "dia": 1,
      "fecha": "YYYY-MM-DD",
      "hora_entrada": "HH:MM",
      "hora_salida": "HH:MM",
      "horas_extras": "0",
      "dia_libre": false
    }
  ]
}

Reglas:
- Los datos de cabecera (trabajador, dni, cargo, empresa, periodo) aplican a todos los registros.
- Un objeto en "registros" por cada fila/día de la tabla.
- Usa formato de 24 horas HH:MM. Si un día es descanso/libre, pon "dia_libre": true.
- Si no puedes leer un campo, omítelo. No inventes datos.
Responde solo el JSON, sin texto adicional."""


def _desde_imagen(img_path: str, ctx_anterior: dict | None = None) -> list:
    """Lee una planilla escaneada/en foto con Groq Vision y arma los registros.

    Reemplaza el OCR espacial (EasyOCR/OpenCV): la IA de visión lee mejor la
    grilla en fotos. Best-effort: si la IA falla, devuelve lista vacía.
    """
    from src.modules.scanner.infrastructure.extractor.ia_fallback import vision_json

    try:
        data = vision_json(img_path, _PROMPT_ASISTENCIA, max_tokens=4096)
    except Exception:
        return []
    return _armar_registros(data, ctx_anterior)


def _armar_registros(data: dict, ctx: dict | None) -> list:
    """Normaliza el JSON de la IA al shape estándar de registro de asistencia."""
    ctx = ctx or {}
    trabajador = data.get("trabajador") or ctx.get("trabajador")
    dni        = data.get("dni") or ctx.get("dni")
    cargo      = data.get("cargo") or ctx.get("cargo")
    empresa    = data.get("empresa") or ctx.get("empresa")
    periodo    = data.get("periodo")
    anio, mes  = _split_periodo(periodo)
    if not anio:
        anio, mes = ctx.get("anio"), ctx.get("mes")
    periodo = periodo or _periodo_str(anio, mes)

    registros = []
    for r in data.get("registros", []) or []:
        if not isinstance(r, dict):
            continue
        entrada   = normalizar_hora(str(r.get("hora_entrada") or ""))
        salida    = normalizar_hora(str(r.get("hora_salida") or ""))
        dia_libre = bool(r.get("dia_libre"))
        if not (entrada or salida or dia_libre):
            continue
        fecha = normalizar_fecha(str(r.get("fecha") or "")) or _fecha_de(anio, mes, r.get("dia"))
        registros.append({
            "trabajador":     trabajador,
            "dni":            dni,
            "cargo":          cargo,
            "departamento":   None,
            "empresa":        empresa,
            "periodo":        periodo,
            "fecha":          fecha,
            "hora_entrada":   entrada,
            "hora_salida":    salida,
            "turno":          _detectar_turno(entrada, salida),
            "horas_normales": None,
            "horas_extras":   _num(r.get("horas_extras")),
            "firma_presente": bool(entrada or salida),
            "dia_libre":      dia_libre,
        })
    return registros


def _fecha_de(anio, mes, dia) -> str | None:
    try:
        if anio and mes and dia:
            return f"{int(anio):04d}-{int(mes):02d}-{int(dia):02d}"
    except (TypeError, ValueError):
        pass
    return None


def _num(valor) -> float | None:
    if valor is None:
        return None
    try:
        s = str(valor).replace(",", ".").strip()
        return float(s) if s and s.lower() not in ("none", "") else None
    except (TypeError, ValueError):
        return None


def _periodo_str(anio, mes) -> str | None:
    if not anio:
        return None
    MESES_INV = {
        "01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO",
        "07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE",
    }
    mes_s = str(mes).zfill(2) if mes else "01"
    return f"{MESES_INV.get(mes_s, mes_s)} {anio}"


def _detectar_turno(entrada: str | None, salida: str | None) -> str | None:
    if not entrada and not salida:
        return None
    # Noche: entrada entre 18-22h y salida entre 4-10h
    h_ent = _hora_a_int(entrada)
    h_sal = _hora_a_int(salida)
    if h_ent is not None and h_sal is not None:
        if 18 <= h_ent <= 23 and 4 <= h_sal <= 10:
            return "Noche"
        if 6 <= h_ent <= 10:
            return "Mañana"
        if 14 <= h_ent <= 17:
            return "Tarde"
    return None


def _hora_a_int(hora_str: str | None) -> int | None:
    if not hora_str:
        return None
    m = re.match(r"(\d{1,2})", hora_str)
    return int(m.group(1)) if m else None


def _split_periodo(periodo: str | None) -> tuple:
    """'ENERO 2024' o '2024-01' → (anio, mes) como strings. Tolerante."""
    if not periodo:
        return None, None
    MESES = {
        "ENERO":"01","FEBRERO":"02","MARZO":"03","ABRIL":"04","MAYO":"05","JUNIO":"06",
        "JULIO":"07","AGOSTO":"08","SEPTIEMBRE":"09","OCTUBRE":"10","NOVIEMBRE":"11",
        "DICIEMBRE":"12","ENE":"01","FEB":"02","MAR":"03","ABR":"04","MAY":"05","JUN":"06",
        "JUL":"07","AGO":"08","SEP":"09","OCT":"10","NOV":"11","DIC":"12",
    }
    up = str(periodo).upper()
    m = re.search(r"(ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO|SEPTIEMBRE|OCTUBRE"
                  r"|NOVIEMBRE|DICIEMBRE|ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC)"
                  r"\D*(\d{4})", up)
    if m:
        return m.group(2), MESES[m.group(1)]
    # Formato ISO parcial 'YYYY-MM'
    partes = str(periodo).split("-")
    if len(partes) >= 2 and partes[0].isdigit():
        return partes[0], partes[1]
    m_yr = re.search(r"(20\d{2})", up)
    return (m_yr.group(1), None) if m_yr else (None, None)


# ===========================================================================
# EXCEL
# ===========================================================================

def _desde_excel(file_path: str) -> list:
    import openpyxl
    wb = openpyxl.load_workbook(file_path, data_only=True)
    ws = wb.active
    filas = list(ws.iter_rows(values_only=True))
    if not filas:
        return []
    encabezados = [str(c).strip().lower() if c is not None else "" for c in filas[0]]
    if _es_formato_horizontal(encabezados):
        return _desde_excel_horizontal(filas, encabezados)
    return _desde_excel_vertical(filas, encabezados)


def _es_formato_horizontal(encabezados: list) -> bool:
    return sum(1 for h in encabezados if re.fullmatch(r"\d{1,2}", h.strip())) >= 10


def _desde_excel_vertical(filas, encabezados) -> list:
    registros = []
    for row in filas[1:]:
        if not any(row):
            continue
        r = _mapear_fila_excel(dict(zip(encabezados, row)))
        if r.get("trabajador"):
            registros.append(r)
    return registros


def _desde_excel_horizontal(filas, encabezados) -> list:
    registros = []
    mes_anio = _detectar_mes_anio_excel(filas)
    anio = mes_anio[0] if mes_anio else None
    mes  = mes_anio[1] if mes_anio else None

    col_dia = {idx: int(h.strip()) for idx, h in enumerate(encabezados)
               if re.fullmatch(r"\d{1,2}", h.strip()) and 1 <= int(h.strip()) <= 31}

    idx_nombre = next((i for i, h in enumerate(encabezados)
                       if any(k in h for k in ["trabajador","nombre","apellido"])), 0)
    idx_dni    = next((i for i, h in enumerate(encabezados)
                       if "dni" in h or "doc" in h), None)

    for row in filas[1:]:
        nombre_raw = row[idx_nombre] if idx_nombre < len(row) else None
        if not nombre_raw:
            continue
        trabajador = str(nombre_raw).strip()
        if not trabajador or trabajador.lower() == "none":
            continue
        dni = str(row[idx_dni]).strip() if idx_dni and idx_dni < len(row) and row[idx_dni] else None

        for col_idx, dia in col_dia.items():
            if col_idx >= len(row):
                continue
            celda = row[col_idx]
            if celda is None or str(celda).strip() in ("", "None"):
                continue
            h_ent, h_sal, turno, h_norm, h_ext, firma = _parsear_celda_horizontal(str(celda).strip())
            if not (h_ent or turno or firma or h_norm):
                continue
            fecha = f"{anio}-{mes:02d}-{dia:02d}" if anio and mes else None
            registros.append({
                "trabajador": trabajador, "dni": dni, "cargo": None, "departamento": None,
                "empresa": None, "periodo": _periodo_str(anio, str(mes).zfill(2) if mes else None),
                "fecha": fecha, "hora_entrada": h_ent, "hora_salida": h_sal,
                "turno": turno, "horas_normales": h_norm, "horas_extras": h_ext,
                "firma_presente": firma, "dia_libre": False,
            })
    return registros


def _parsear_celda_horizontal(celda: str):
    hora_entrada = hora_salida = turno = horas_n = horas_e = None
    firma = False
    if re.fullmatch(r"[XxPpSs1]", celda.strip()):
        return hora_entrada, hora_salida, turno, horas_n, horas_e, True
    m = re.match(r"(\d{1,2}:\d{2})\s*[-/]\s*(\d{1,2}:\d{2})", celda)
    if m:
        return m.group(1)+":00", m.group(2)+":00", turno, horas_n, horas_e, True
    m = re.fullmatch(r"(\d{1,2}(?:\.\d{1,2})?)", celda.strip())
    if m:
        return hora_entrada, hora_salida, turno, float(m.group(1)), horas_e, True
    m = re.search(r"(Ma[ñn]ana|Tarde|Noche|Diurno|Nocturno)", celda, re.IGNORECASE)
    if m:
        return hora_entrada, hora_salida, m.group(1).capitalize(), horas_n, horas_e, True
    return hora_entrada, hora_salida, turno, horas_n, horas_e, firma


def _detectar_mes_anio_excel(filas) -> tuple | None:
    MESES = {"ENERO":1,"FEBRERO":2,"MARZO":3,"ABRIL":4,"MAYO":5,"JUNIO":6,
             "JULIO":7,"AGOSTO":8,"SEPTIEMBRE":9,"OCTUBRE":10,"NOVIEMBRE":11,"DICIEMBRE":12}
    for row in filas[:5]:
        for celda in row:
            if celda is None:
                continue
            texto = str(celda).upper()
            for nombre, num in MESES.items():
                if nombre in texto:
                    m_yr = re.search(r"(20\d{2})", texto)
                    if m_yr:
                        return (int(m_yr.group(1)), num)
    return None


def _mapear_fila_excel(fila: dict) -> dict:
    def buscar(claves):
        for clave in claves:
            for k, v in fila.items():
                if clave in k.lower():
                    return v
        return None

    trabajador  = buscar(["trabajador","nombre","apellidos y nombres","apellidos"])
    dni         = buscar(["dni","documento","doc"])
    cargo       = buscar(["cargo","puesto","funcion"])
    depto       = buscar(["departamento","area","seccion","unidad"])
    fecha_raw   = buscar(["fecha","date","dia"])
    entrada_raw = buscar(["entrada","ingreso","hora entrada","hora_entrada"])
    salida_raw  = buscar(["salida","hora salida","hora_salida"])
    turno       = buscar(["turno","jornada"])
    horas_n_raw = buscar(["horas normales","horas_normales","horas trabajadas"])
    horas_e_raw = buscar(["horas extras","horas_extras","sobretiempo"])
    firma_raw   = buscar(["firma","asistencia","presente","asistio"])

    return {
        "trabajador":     str(trabajador).strip() if trabajador else None,
        "dni":            str(dni).strip() if dni else None,
        "cargo":          str(cargo).strip() if cargo else None,
        "departamento":   str(depto).strip() if depto else None,
        "empresa":        None, "periodo": None,
        "fecha":          _normalizar_fecha_valor(fecha_raw),
        "hora_entrada":   _normalizar_hora_valor(entrada_raw),
        "hora_salida":    _normalizar_hora_valor(salida_raw),
        "turno":          str(turno).strip() if turno else None,
        "horas_normales": float(horas_n_raw) if horas_n_raw else None,
        "horas_extras":   float(horas_e_raw) if horas_e_raw else None,
        "firma_presente": _es_presente(firma_raw),
        "dia_libre":      False,
    }


def _normalizar_fecha_valor(valor) -> str | None:
    if valor is None:
        return None
    import datetime
    if isinstance(valor, (datetime.date, datetime.datetime)):
        return valor.strftime("%Y-%m-%d")
    return normalizar_fecha(str(valor))


def _normalizar_hora_valor(valor) -> str | None:
    if valor is None:
        return None
    import datetime
    if isinstance(valor, datetime.time):
        return valor.strftime("%H:%M:%S")
    if isinstance(valor, datetime.datetime):
        return valor.strftime("%H:%M:%S")
    return normalizar_hora(str(valor))


def _es_presente(valor) -> bool:
    if valor is None:
        return False
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in {"si","s","x","p","presente","1","true","asistio"}


# ===========================================================================
# TEXTO PLANO (PDF embebido)
# ===========================================================================

def _desde_texto(texto: str) -> list:
    registros = []
    for linea in texto.splitlines():
        r = _parsear_linea(linea)
        if r:
            registros.append(r)
    if not registros and texto.strip():
        parcial = _parsear_modo_cabecera(texto)
        if parcial:
            registros.append(parcial)
    return registros


def _parsear_linea(linea: str) -> dict | None:
    m_dni = re.search(r"\b(\d{8})\b", linea)
    if not m_dni:
        return None
    dni = m_dni.group(1)
    trabajador = _extraer_nombre_linea(linea)
    if not trabajador:
        return None
    horas = re.findall(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", linea)
    return {
        "trabajador": trabajador, "dni": dni, "cargo": None, "departamento": None,
        "empresa": None, "periodo": None,
        "fecha": normalizar_fecha(linea),
        "hora_entrada": horas[0] if horas else None,
        "hora_salida":  horas[1] if len(horas) > 1 else None,
        "turno": None, "horas_normales": None, "horas_extras": None,
        "firma_presente": bool(re.search(r"\b(Si|X|P|Presente)\b", linea, re.IGNORECASE)),
        "dia_libre": False,
    }


def _parsear_modo_cabecera(texto: str) -> dict | None:
    dni_m = re.search(r"\b(\d{8})\b", texto)
    if not dni_m:
        return None
    nombre_m = re.search(
        r"(?:APELLIDOS?\s+Y?\s*NOMBRES?|TRABAJADOR)[:\s]*\n?\s*([A-ZÁÉÍÓÚÑ][^\n\d]{5,60})",
        texto, re.IGNORECASE
    )
    if not nombre_m:
        return None
    return {
        "trabajador": nombre_m.group(1).strip(), "dni": dni_m.group(1),
        "cargo": None, "departamento": None, "empresa": None, "periodo": None,
        "fecha": None, "hora_entrada": None, "hora_salida": None, "turno": None,
        "horas_normales": None, "horas_extras": None,
        "firma_presente": False, "dia_libre": False,
    }


def _extraer_nombre_linea(linea: str) -> str | None:
    m = re.match(
        r"^([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,3})",
        linea
    )
    return m.group(1).strip() if m else None
