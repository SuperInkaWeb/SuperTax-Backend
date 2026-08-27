# Integración con Groq (visión IA para el Scanner)

El módulo Scanner usa **Groq** como fallback de IA cuando el OCR/heurísticas no
bastan para extraer datos de un documento — por ejemplo, una **planilla de
asistencia fotografiada** o un comprobante con layout difícil.

## Dónde

`modules/scanner/infrastructure/extractor/ia_fallback.py`. Se invoca desde el
extractor cuando el camino determinista no rinde. Devuelve JSON estructurado que
el módulo mapea a sus campos.

## Modelo

Modelo de **visión** de Groq, por defecto **Qwen** (`qwen/qwen3.6-27b`),
configurable por variable de entorno. Se le pasa la imagen del documento y un
prompt que pide los campos en JSON.

> **Histórico:** se evaluó Llama 4 de Meta pero está descontinuado; el modelo
> vigente es el de **Qwen**. Antes había OCR con **EasyOCR** (PyTorch), eliminado
> por peso; el camino de imagen escaneada usa ahora Groq Vision.

## Variables

| Variable | Default | Uso |
|---|---|---|
| `GROQ_API_KEY` | — (obligatoria para el fallback IA) | autenticación con Groq |
| `GROQ_MODEL` | `qwen/qwen3.6-27b` | modelo de visión a usar |

Si `GROQ_API_KEY` no está configurada, el fallback IA lanza un error claro; el
resto del Scanner (OCR/heurísticas) sigue funcionando. En producción se define en
Railway (la usa el `worker-scanner`).

## Dependencia

`groq>=0.13.0` (declarada en `pyproject.toml`). El cliente se importa de forma
perezosa dentro de `ia_fallback.py` para no cargarlo si no se usa.
