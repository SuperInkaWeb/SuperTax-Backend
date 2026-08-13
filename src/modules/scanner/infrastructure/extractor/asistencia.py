"""
asistencia.py — Extrae registros de planillas de asistencia laborales.

Formatos soportados:
  1. Excel (.xlsx/.xls) — vertical o horizontal (días como columnas)
  2. PDF escaneado / imagen — detección de tabla con OpenCV + EasyOCR espacial
  3. PDF con texto embebido — parsing de layout tabular

Campos por registro:
  trabajador, dni, cargo, departamento, empresa, periodo,
  fecha, hora_entrada, hora_salida, turno,
  horas_normales, horas_extras, firma_presente, dia_libre
"""
import os
import re
import tempfile
from src.modules.scanner.infrastructure.utils import limpiar_texto, normalizar_fecha, normalizar_hora, pdf_a_texto, imagen_a_texto, EXTENSIONES_IMAGEN
from src.modules.scanner.infrastructure.config import TESSERACT_CMD

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
# IMAGEN ESCANEADA — EasyOCR + Tesseract header
# ===========================================================================

def _desde_imagen(img_path: str, ctx_anterior: dict | None = None) -> list:
    """
    Estrategia:
      1. Detectar líneas horizontales de la tabla con OpenCV.
      2. Zona superior (antes de la tabla) -> Tesseract PSM 6 para extraer
         nombre, DNI, cargo, empresa, mes/año.
      3. Zona de datos -> EasyOCR para detectar números de día y horas.
      4. Emparejar cada número de día con los valores de hora más cercanos
         por posición horizontal (X) dentro de ±2 alturas de fila.
    """
    import cv2
    import numpy as np
    import pytesseract
    from PIL import Image

    # ── 1. Configurar Tesseract ──────────────────────────────────────────────
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    # ── 2. Cargar imagen y escalar a >= 2500px de ancho ─────────────────────
    img_orig = cv2.imread(img_path)
    if img_orig is None:
        return []
    h_orig, w_orig = img_orig.shape[:2]
    scale = max(1.0, 2500 / w_orig)
    img = cv2.resize(img_orig, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gris.shape

    # ── 3. Detectar lineas horizontales para encontrar inicio de tabla ───────
    _, bin_inv = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    k_h = cv2.getStructuringElement(cv2.MORPH_RECT, (w // 8, 1))
    lineas_h = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, k_h)
    proj_h = np.sum(lineas_h > 0, axis=1).astype(float)
    umbral_h = w * 0.20
    y_lines = _picos(proj_h, umbral_h)

    # El inicio de la tabla es la primera linea horizontal detectada
    y_tabla = y_lines[0] if y_lines else h // 4

    # ── 4. OCR del encabezado AMPLIADO con Tesseract ─────────────────────────
    # El nombre, DNI y mes/año suelen estar en los encabezados de la tabla,
    # no solo en la zona pre-tabla. Usamos hasta y_lines[7] (o el primer 35%
    # de la imagen) como zona de encabezado.
    y_header_fin = y_lines[7] if len(y_lines) > 7 else int(h * 0.35)
    y_header_fin = min(y_header_fin, int(h * 0.45))  # no pasar del 45%
    header_img = img[0: max(1, y_header_fin), :]
    header_gris = cv2.cvtColor(header_img, cv2.COLOR_BGR2GRAY)
    header_bin = cv2.adaptiveThreshold(
        header_gris, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10
    )
    texto_header = pytesseract.image_to_string(
        Image.fromarray(header_bin), lang="spa", config="--psm 6 --oem 3"
    )

    trabajador = _extraer_nombre(texto_header)
    dni        = _extraer_dni_txt(texto_header)
    cargo      = _extraer_campo(texto_header, ["CARGO", "PUESTO"])
    empresa    = _extraer_empresa(texto_header)
    periodo    = _extraer_mes_anio_txt(texto_header)
    anio, mes  = _split_periodo(periodo)

    # ── 5. EasyOCR en toda la imagen para capturar dia, horas ────────────────
    import easyocr
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    proc = clahe.apply(gris)
    proc = cv2.bilateralFilter(proc, 9, 75, 75)
    _, proc_bin = cv2.threshold(proc, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    ocr_tmp = img_path.replace(".png", "_ocr.png").replace(".jpg", "_ocr.png")
    cv2.imwrite(ocr_tmp, proc_bin)

    reader = easyocr.Reader(["es", "en"], gpu=False)
    resultados = reader.readtext(ocr_tmp, detail=1)
    if os.path.exists(ocr_tmp):
        os.remove(ocr_tmp)

    tokens = []
    for (bbox, texto, conf) in resultados:
        if not texto.strip():
            continue
        x_c = (bbox[0][0] + bbox[2][0]) / 2
        y_c = (bbox[0][1] + bbox[2][1]) / 2
        tokens.append({"texto": texto.strip(), "x": x_c, "y": y_c, "conf": conf})

    # ── 6. Estimar altura de fila ────────────────────────────────────────────
    if len(y_lines) >= 3:
        row_gaps = [y_lines[i+1] - y_lines[i] for i in range(len(y_lines)-1)
                    if y_lines[i+1] - y_lines[i] > 30]
        row_h = sorted(row_gaps)[len(row_gaps)//2] if row_gaps else 120
    else:
        row_h = 120

    # ── 7. Identificar columnas X por los encabezados de columna ────────────
    x_cols = _detectar_columnas_x(tokens, y_tabla, row_h)

    # Si no hay x_entrada/x_salida pero hay tokens de hora,
    # estimar las columnas X como el percentil 25 y 75 de las X de horas
    if ("hora_entrada" not in x_cols or "hora_salida" not in x_cols):
        hora_xs = sorted(
            tok["x"] for tok in tokens
            if tok["y"] > y_tabla and _parsear_hora_token(tok["texto"])
        )
        if len(hora_xs) >= 4:
            mid = len(hora_xs) // 2
            x_cols.setdefault("hora_entrada", hora_xs[mid // 2])
            x_cols.setdefault("hora_salida",  hora_xs[mid + mid // 2])

    # ── 8. Fallback: extraer mes/anio y trabajador de los tokens EasyOCR ──────
    if not periodo or not anio:
        for tok in tokens:
            if not periodo:
                periodo = _extraer_mes_anio_txt(tok["texto"])
                if periodo:
                    anio, mes = _split_periodo(periodo)
            if periodo:
                break
    # Intentar también combinando tokens cercanos en Y (mes + año suelen estar juntos)
    if not periodo:
        for i, tok in enumerate(tokens):
            txt_combo = tok["texto"]
            # Combinar con tokens cercanos
            for tok2 in tokens:
                if abs(tok2["y"] - tok["y"]) < 60 and tok2 is not tok:
                    txt_combo += " " + tok2["texto"]
            p = _extraer_mes_anio_txt(txt_combo)
            if p:
                periodo = p
                anio, mes = _split_periodo(p)
                break

    if not trabajador:
        # Buscar en tokens de EasyOCR cerca del área de encabezado
        for tok in tokens:
            if tok["y"] > y_header_fin:
                continue
            n = _extraer_nombre(tok["texto"])
            if n and len(n.split()) >= 2:
                trabajador = n
                break

    if not empresa:
        # Limpiar empresa del header Tesseract si contiene SAC/SRL
        for linea in texto_header.splitlines():
            linea = linea.strip()
            if re.search(r"\b(SAC|SRL|S\.A\.C\.|S\.A\.|EIRL)\b", linea, re.IGNORECASE):
                empresa = re.sub(r"[^\w\s\.,]", "", linea).strip()
                break

    # ── 9. Heredar contexto de página anterior si no se encontró info ────────
    if ctx_anterior:
        trabajador = trabajador or ctx_anterior.get("trabajador")
        dni        = dni        or ctx_anterior.get("dni")
        cargo      = cargo      or ctx_anterior.get("cargo")
        empresa    = empresa    or ctx_anterior.get("empresa")
        if not anio:
            anio = ctx_anterior.get("anio")
            mes  = ctx_anterior.get("mes")
            periodo = _periodo_str(anio, mes) if (anio and mes) else periodo

    # ── 10. Extraer registros por numero de dia ───────────────────────────────
    return _extraer_por_dia(
        tokens, x_cols, row_h, y_tabla,
        trabajador, dni, cargo, empresa, anio, mes
    )


# ── Detección de columnas ─────────────────────────────────────────────────────

def _detectar_columnas_x(tokens: list, y_tabla: float, row_h: float) -> dict:
    zona_min = y_tabla - row_h
    zona_max = y_tabla + 2.5 * row_h

    KWS = {
        "fecha":        ["FECHA", "DIA", "DÍA", "N°", "NUM"],
        "hora_entrada": ["ENTRADA", "INGRESO", "H.E", "HORA E"],
        "hora_salida":  ["SALIDA", "EGRESO", "H.S", "HORA S"],
        "horas_extras": ["H.E.", "HRS", "EXTRA", "SOBRE"],
        "firma_trab":   ["TRABAJADOR", "TRAB", "FIRMA", "F.T"],
        "firma_sup":    ["SUPERVISOR", "SUP", "VB", "V.B"],
    }
    cols = {}
    for tok in tokens:
        if not (zona_min <= tok["y"] <= zona_max):
            continue
        txt = tok["texto"].upper()
        for campo, palabras in KWS.items():
            if any(p in txt for p in palabras) and campo not in cols:
                cols[campo] = tok["x"]
    return cols


# ── Extracción de registros ───────────────────────────────────────────────────

_RE_HORA_SIMPLE = re.compile(
    r"(?:"
    r"(\d{1,2})\s*[:\.]\s*(\d{2})\s*(AM|PM|am|pm)?"
    r"|(\d{1,2})\s*(AM|PM|am|pm)"
    r")"
)

def _parsear_hora_token(texto: str) -> str | None:
    # Normalizar caracteres comunes de OCR
    txt = texto.upper()
    txt = txt.replace("B", "8").replace("O", "0").replace("I", "1").replace("|", "")
    txt = re.sub(r"[^0-9.:AMPM]", "", txt)

    m = _RE_HORA_SIMPLE.search(txt)
    if not m:
        return None
    g = m.groups()
    if g[0] is not None:
        h, mn = int(g[0]), int(g[1])
        ampm = (g[2] or "").upper()
        if ampm == "PM" and h < 12: h += 12
        elif ampm == "AM" and h == 12: h = 0
        if not (0 <= h <= 23 and 0 <= mn <= 59):
            return None
        return f"{h:02d}:{mn:02d}:00"
    if g[3] is not None:
        h = int(g[3])
        ampm = (g[4] or "").upper()
        if ampm == "PM" and h < 12: h += 12
        elif ampm == "AM" and h == 12: h = 0
        return f"{h:02d}:00:00"
    return None


def _es_dia_libre(texto: str) -> bool:
    txt = texto.upper()
    return any(k in txt for k in ["LIBRE", "DESCANSO", "VACAC", "FERI", "LIBR", "D.L"])


def _extraer_por_dia(tokens, x_cols, row_h, y_tabla, trabajador, dni, cargo, empresa, anio, mes) -> list:
    """
    Dos estrategias combinadas:
    1. Clave por número de día detectado (EasyOCR)
    2. Clave por posición Y (cuando EasyOCR no detecta el número)
    """
    x_entrada = x_cols.get("hora_entrada")
    x_salida  = x_cols.get("hora_salida")
    x_extras  = x_cols.get("horas_extras")
    x_firma   = x_cols.get("firma_trab") or x_cols.get("firma_sup")

    # ── A. Construir mapa de fila → contenido usando posición Y ──────────────
    # Encontrar todos los tokens de hora (son la señal más confiable)
    # Agruparlos por fila usando clustering simple de Y
    hora_tokens = []
    for tok in tokens:
        if tok["y"] <= y_tabla:
            continue
        h = _parsear_hora_token(tok["texto"])
        if h:
            hora_tokens.append({"hora": h, "y": tok["y"], "x": tok["x"], "conf": tok["conf"]})

    # ── B. Detectar números de día con normalización OCR ────────────────────
    dia_tokens = []
    for tok in tokens:
        if tok["y"] <= y_tabla:
            continue
        txt = tok["texto"].strip().upper()
        # Normalizar: I→1, O→0
        txt_n = re.sub(r"(?<!\d)I(?!\d)", "1", txt)
        txt_n = txt_n.replace("O", "0")
        num_s = re.sub(r"[^\d]", "", txt_n)
        # Solo si el token original tiene ≤ 3 caracteres y el resultado es 1-2 dígitos
        if not num_s or len(num_s) > 2 or len(re.sub(r"[^\d]", "", tok["texto"])) > 2:
            continue
        n = int(num_s)
        if not (1 <= n <= 31):
            continue
        dia_tokens.append({"dia": n, "y": tok["y"], "x": tok["x"]})

    # ── C. Inferir posiciones de fila si hay pocas detecciones de día ────────
    # Tomar el Y de las horas detectadas y agrupar por cercanía
    filas_y: list[float] = []
    if hora_tokens:
        ys_horas = sorted(t["y"] for t in hora_tokens)
        grupo_y = [ys_horas[0]]
        for y in ys_horas[1:]:
            if y - grupo_y[-1] < row_h * 0.4:
                grupo_y.append(y)
            else:
                filas_y.append(sum(grupo_y) / len(grupo_y))
                grupo_y = [y]
        filas_y.append(sum(grupo_y) / len(grupo_y))

    # También agregar los Y de los días detectados
    for d in dia_tokens:
        if not any(abs(d["y"] - fy) < row_h * 0.5 for fy in filas_y):
            filas_y.append(d["y"])
    filas_y.sort()

    # ── D. Asignar número de día a cada fila ─────────────────────────────────
    # Mapa de Y_fila → dia_asignado
    y_a_dia: dict[float, int] = {}
    for fy in filas_y:
        # Buscar día detectado dentro de row_h
        candidatos = [d for d in dia_tokens if abs(d["y"] - fy) < row_h * 0.6]
        if candidatos:
            # El más confiable: el que tiene X más pequeña (columna FECHA es la primera)
            best = min(candidatos, key=lambda d: d["x"])
            y_a_dia[fy] = best["dia"]

    # ── E. Construir registros por fila ──────────────────────────────────────
    registros = []
    seen_dias = set()

    for fy in filas_y:
        dia = y_a_dia.get(fy)
        if dia and dia in seen_dias:
            continue
        if dia:
            seen_dias.add(dia)

        margen_y = row_h * 0.65
        fila_toks = [t for t in tokens
                     if abs(t["y"] - fy) <= margen_y and t["y"] > y_tabla]

        hora_entrada = hora_salida = horas_extras = None
        firma_trab = dia_libre = False

        for tok in fila_toks:
            txt = tok["texto"]

            if _es_dia_libre(txt):
                dia_libre = True
                continue

            hora = _parsear_hora_token(txt)
            if hora:
                if x_entrada and x_salida:
                    d_ent = abs(tok["x"] - x_entrada)
                    d_sal = abs(tok["x"] - x_salida)
                    if d_ent <= d_sal:
                        hora_entrada = hora_entrada or hora
                    else:
                        hora_salida = hora_salida or hora
                else:
                    # Sin referencia X: primera hora = entrada, segunda = salida
                    if hora_entrada is None:
                        hora_entrada = hora
                    else:
                        hora_salida = hora_salida or hora

            if x_extras and abs(tok["x"] - x_extras) < row_h:
                m_num = re.fullmatch(r"(\d+(?:[.,]\d+)?)", txt.strip())
                if m_num:
                    try:
                        horas_extras = float(m_num.group(1).replace(",", "."))
                    except Exception:
                        pass

            if x_firma and abs(tok["x"] - x_firma) < row_h * 2 and len(txt.strip()) >= 2:
                firma_trab = True

        firma_presente = bool(hora_entrada or hora_salida or firma_trab)
        if not (firma_presente or dia_libre):
            continue

        fecha = None
        if dia and anio and mes:
            try:
                fecha = f"{anio}-{int(mes):02d}-{dia:02d}"
            except Exception:
                pass

        registros.append({
            "trabajador":     trabajador,
            "dni":            dni,
            "cargo":          cargo,
            "departamento":   None,
            "empresa":        empresa,
            "periodo":        _periodo_str(anio, mes),
            "fecha":          fecha,
            "hora_entrada":   hora_entrada,
            "hora_salida":    hora_salida,
            "turno":          _detectar_turno(hora_entrada, hora_salida),
            "horas_normales": None,
            "horas_extras":   horas_extras,
            "firma_presente": firma_presente,
            "dia_libre":      dia_libre,
        })

    return registros


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
    txt = ((entrada or "") + " " + (salida or ""))
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


# ── Detección de picos ────────────────────────────────────────────────────────

def _picos(proyeccion, umbral: float) -> list[int]:
    picos = []
    en_pico = False
    inicio = 0
    for i, v in enumerate(proyeccion):
        if v >= umbral and not en_pico:
            en_pico = True
            inicio = i
        elif v < umbral and en_pico:
            en_pico = False
            picos.append((inicio + i) // 2)
    if en_pico:
        picos.append((inicio + len(proyeccion)) // 2)
    return picos


# ── Extraccion de texto de encabezado ─────────────────────────────────────────

def _extraer_nombre(texto: str) -> str | None:
    m = re.search(
        r"(?:APELLIDOS?\s+Y?\s*NOMBRES?|TRABAJADOR|NOMBRES?)\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ][^\n\d]{5,60})",
        texto, re.IGNORECASE
    )
    if m:
        return _limpiar_nombre(m.group(1))
    for linea in texto.splitlines():
        linea = linea.strip()
        if re.match(r'^[A-ZÁÉÍÓÚÑ]{3,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,}){1,4}$', linea):
            EXCLUIR = {"CARGO","PLANILLA","REGISTRO","SERVICIOS","EXPLOTACION",
                       "CONTROL","ASISTENCIA","RUC","MINEROS","YACIMIENTOS","BULLMINING"}
            if not any(k in linea for k in EXCLUIR):
                return linea
    return None


def _limpiar_nombre(nombre: str) -> str:
    return re.split(r"[\n\d]", nombre)[0].strip()


def _extraer_dni_txt(texto: str) -> str | None:
    m = re.search(r"\bDNI\b[:\s]*(\d{8})\b", texto, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{8})\b", texto)
    return m.group(1) if m else None


def _extraer_empresa(texto: str) -> str | None:
    m = re.search(r"(?:EMPRESA|RAZÓN SOCIAL|RAZON SOCIAL)\s*[:\-]?\s*([^\n]{5,60})", texto, re.IGNORECASE)
    if m:
        return _limpiar_str_ocr(m.group(1))
    for linea in texto.splitlines():
        linea = linea.strip()
        if re.search(r"\b(SAC|SRL|S\.A\.C\.|S\.A\.|EIRL|LTDA)\b", linea, re.IGNORECASE):
            return _limpiar_str_ocr(linea[:80])
    return None


def _limpiar_str_ocr(texto: str) -> str:
    """Elimina caracteres no imprimibles y símbolos OCR basura."""
    # Reemplazar caracteres basura (incluye U+FFFD y similares)
    limpio = re.sub(r"[^\x20-\x7EÁÉÍÓÚáéíóúÑñ]", " ", texto)
    # Quitar símbolos que no son letras, dígitos, espacios ni puntuación básica
    limpio = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚáéíóúÑñ\s\.,\-&/]", "", limpio)
    limpio = re.sub(r"\s{2,}", " ", limpio).strip()
    return limpio


def _extraer_campo(texto: str, keywords: list) -> str | None:
    for kw in keywords:
        m = re.search(rf"{kw}\s*[:\-]?\s*([^\n]{{2,40}})", texto, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


_MESES_MAP = {
    "ENERO":"01","FEBRERO":"02","MARZO":"03","ABRIL":"04","MAYO":"05","JUNIO":"06",
    "JULIO":"07","AGOSTO":"08","SEPTIEMBRE":"09","OCTUBRE":"10","NOVIEMBRE":"11","DICIEMBRE":"12",
    "ENE":"01","FEB":"02","MAR":"03","ABR":"04","MAY":"05","JUN":"06",
    "JUL":"07","AGO":"08","SEP":"09","OCT":"10","NOV":"11","DIC":"12",
}

def _extraer_mes_anio_txt(texto: str) -> str | None:
    patron = (r"(ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO|SEPTIEMBRE|OCTUBRE"
              r"|NOVIEMBRE|DICIEMBRE|ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC)"
              r"[^\d]*(\d{4})")
    m = re.search(patron, texto.upper())
    if m:
        return f"{m.group(2)}-{_MESES_MAP[m.group(1)]}"
    m_yr = re.search(r"\b(202\d)\b", texto)
    return f"{m_yr.group(1)}-01" if m_yr else None


def _split_periodo(periodo: str | None) -> tuple:
    if not periodo:
        return None, None
    partes = periodo.split("-")
    if len(partes) >= 2:
        return partes[0], partes[1]
    return partes[0], None


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
