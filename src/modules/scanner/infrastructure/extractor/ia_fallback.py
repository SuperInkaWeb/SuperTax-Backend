"""
ia_fallback.py
--------------
Ruta 2 de extracción: cuando el OCR tradicional (Tesseract + EasyOCR)
no puede leer el documento, se envía la imagen a Groq Vision (LLaMA 4)
para que intente reconstruir el contenido.

IMPORTANTE: Los datos extraídos por esta ruta pueden contener errores
debido al mal estado del documento original. Siempre se marca con
procesado_con_ia=True para que el frontend muestre la advertencia.
"""

import os
import json
import base64
import re


def _inferir_tipo(texto: str) -> str:
    """Intenta inferir el tipo de documento desde texto libre si el JSON falló."""
    t = texto.lower()
    if any(x in t for x in ["factura", "invoice"]): return "factura_electronica"
    if any(x in t for x in ["boleta"]): return "boleta_venta"
    if any(x in t for x in ["honorarios"]): return "recibo_honorarios"
    if any(x in t for x in ["kwh", "suministro", "luz", "energia"]): return "recibo_luz"
    if any(x in t for x in ["m3", "agua", "sedapal"]): return "recibo_agua"
    if any(x in t for x in ["gas", "calidda", "contugas"]): return "recibo_gas"
    if any(x in t for x in ["telefon", "movistar", "claro", "entel"]): return "recibo_telefonia"
    if any(x in t for x in ["asistencia", "planilla", "entrada", "salida"]): return "asistencia"
    return "desconocido"


def _imagen_a_base64(imagen_path: str) -> str:
    with open(imagen_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def ia_leer_documento(imagen_path: str) -> dict:
    """
    Envía la imagen a Groq Vision y solicita extracción estructurada.

    Retorna un dict con:
      - tipo_documento: tipo detectado
      - campos: dict con los campos extraídos
      - procesado_con_ia: True (siempre)
      - advertencia: mensaje para el usuario
    """
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY no está configurada en .env")

    client = Groq(api_key=api_key)

    imagen_b64 = _imagen_a_base64(imagen_path)

    prompt = """Eres un asistente especializado en documentos peruanos.
Analiza esta imagen de un documento y extrae toda la información que puedas leer.

El documento puede ser uno de estos tipos:
- factura_electronica
- boleta_venta
- recibo_honorarios
- nota_credito
- nota_debito
- recibo_luz
- recibo_agua
- recibo_gas
- recibo_telefonia
- asistencia (planilla de control de asistencia laboral)

Responde ÚNICAMENTE con un JSON válido con esta estructura exacta:
{
  "tipo_documento": "el tipo detectado",
  "confianza_lectura": "alta|media|baja",
  "campos": {
    "campo1": "valor1",
    "campo2": "valor2"
  }
}

Extrae todos los campos que puedas leer: nombres, RUC, fechas, montos, horas, etc.
Si el documento está muy deteriorado y no puedes leer algo, omite ese campo.
No inventes datos. Solo incluye lo que puedes leer en la imagen.
Responde solo el JSON, sin texto adicional."""

    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "qwen/qwen3.6-27b"),
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{imagen_b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ],
        reasoning_effort="none",
        response_format={"type": "json_object"},
        max_completion_tokens=2048,
    )

    texto = response.choices[0].message.content.strip()

    # Limpiar posibles ```json ... ``` que el modelo a veces agrega
    texto = re.sub(r"^```(?:json)?\s*", "", texto)
    texto = re.sub(r"\s*```$", "", texto)

    try:
        resultado = json.loads(texto)
    except json.JSONDecodeError:
        # Intentar extraer el bloque JSON del texto
        match = re.search(r"\{.*\}", texto, re.DOTALL)
        if match:
            try:
                resultado = json.loads(match.group(0))
            except json.JSONDecodeError:
                # Último intento: construir resultado básico desde el texto libre
                resultado = {
                    "tipo_documento": _inferir_tipo(texto),
                    "confianza_lectura": "baja",
                    "campos": {"texto_extraido": texto[:1000]}
                }
        else:
            resultado = {
                "tipo_documento": _inferir_tipo(texto),
                "confianza_lectura": "baja",
                "campos": {"texto_extraido": texto[:1000]}
            }

    return {
        "tipo_documento":    resultado.get("tipo_documento", "desconocido"),
        "confianza_lectura": resultado.get("confianza_lectura", "baja"),
        "campos":            resultado.get("campos", {}),
        "procesado_con_ia":  True,
        "advertencia": (
            "⚠️ Este documento fue procesado con inteligencia artificial "
            "porque estaba en mal estado o era ilegible. "
            "Los datos extraídos pueden contener errores. "
            "Verifica la información manualmente antes de usarla."
        )
    }
