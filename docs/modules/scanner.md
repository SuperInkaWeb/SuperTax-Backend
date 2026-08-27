# Módulo Scanner — extracción OCR + IA

Toma documentos (PDF o imagen), detecta su tipo y extrae sus campos a datos
estructurados (para exportar a Excel). Cubre comprobantes y recibos de servicios,
más documentos laborales (asistencia, boleta de pago).

## Flujo

```
1. Sube un documento (o elige el tipo manualmente)
2. Se encola un job de extracción
   worker: extrae texto (OCR si hace falta) → clasifica → extrae campos
3. Ve el resultado estructurado y lo exporta a Excel (todo junto o por documento)
```

## Extracción de texto (`infrastructure/utils/`)

| Archivo | Rol |
|---|---|
| `pdf.py` | abre PDFs (PyMuPDF / pdfplumber); texto embebido si existe |
| `ocr.py` | OCR con **Tesseract** (`pytesseract`) para páginas escaneadas |
| `text.py` | normalización y utilidades de texto |

Documentos "digitales" (con capa de texto) se leen directo; escaneados pasan por
Tesseract (español).

## Clasificación y tipos (`infrastructure/extractor/`)

`clasificador.py` decide el tipo; cada tipo tiene su extractor:

```
comprobante.py       factura / boleta (RUC, serie, total, descripción de ítems…)
recibo_agua.py       recibo de agua
recibo_luz.py        recibo de luz
recibo_gas.py        recibo de gas
recibo_telefonia.py  recibo de telefonía
asistencia.py        planilla de asistencia (multi-registro)
boleta_pago.py       boleta de pago laboral (multi-registro)
campos.py            helpers de extracción de campos comunes
```

Los tipos **multi-registro** (asistencia, boleta de pago) devuelven varias filas
por documento, no un solo conjunto de campos.

## Fallback con IA (`extractor/ia_fallback.py`) — Groq Vision

Cuando el OCR/heurísticas no bastan (p. ej. una **planilla fotografiada**), se usa
**Groq Vision** (modelo Qwen) para leer la imagen y devolver JSON estructurado.
Ver [integrations/groq.md](../integrations/groq.md).

> **Histórico:** antes existía una dependencia de **EasyOCR** (PyTorch) que se
> eliminó por peso y por no estar declarada. El camino de imagen escaneada de
> asistencia ahora usa Groq Vision.

## Exportación (`infrastructure/report_documentos.py`)

Genera el Excel de resultados con openpyxl, en dos modos:

- **Todo junto**: una hoja con todas las filas de todos los documentos.
- **Por documento**: una hoja por archivo.

Incluye una columna *Archivo* para rastrear el origen de cada fila.

## Jobs

Igual patrón que el resto: encola en Postgres, lo procesa `workers/scanner_worker.py`
(ver [architecture/async-jobs.md](../architecture/async-jobs.md)). La
configuración del módulo (ej. ruta de Tesseract) vive en `infrastructure/config.py`
y en `SCANNER_TESSERACT_CMD`.
