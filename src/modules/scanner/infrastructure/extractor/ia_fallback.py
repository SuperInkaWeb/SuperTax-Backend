"""
ia_fallback.py
--------------
Ruta de extracción por IA de visión (Groq): cuando el OCR tradicional
(Tesseract) no puede leer el documento, se envía la imagen al modelo de visión
para reconstruir el contenido.

`vision_json` es el primitivo reutilizable (imagen + prompt → JSON). Lo usan
tanto el fallback general (`ia_leer_documento`) como la lectura de planillas de
asistencia escaneadas (`asistencia._desde_imagen`).

IMPORTANTE: los datos por esta ruta pueden contener errores por el estado del
documento; el fallback general marca `procesado_con_ia=True` para que el frontend
muestre la advertencia.
"""

import base64
import json
import os
import re


def _imagen_a_base64(imagen_path: str) -> str:
    with open(imagen_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _groq_client():
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY no está configurada en .env")
    return Groq(api_key=api_key)


def vision_json(imagen_path: str, prompt: str, max_tokens: int = 2048) -> dict:
    """Envía la imagen + prompt a Groq Vision y devuelve el JSON parseado.

    Rescata el bloque `{...}` si el modelo agrega texto extra. Lanza
    `json.JSONDecodeError` si no hay JSON, o `ValueError` si falta la API key.
    """
    client = _groq_client()
    imagen_b64 = _imagen_a_base64(imagen_path)

    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "qwen/qwen3.6-27b"),
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{imagen_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        reasoning_effort="none",
        response_format={"type": "json_object"},
        max_completion_tokens=max_tokens,
    )

    texto = (response.choices[0].message.content or "").strip()
    # Salvaguarda: quitar razonamiento <think> y cercos ```json ... ```.
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL).strip()
    texto = re.sub(r"^```(?:json)?\s*", "", texto)
    texto = re.sub(r"\s*```$", "", texto)

    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", texto, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def ia_leer_documento(imagen_path: str) -> dict:
    """
    Fallback general: envía la imagen a Groq Vision y solicita clasificación +
    extracción de campos. Devuelve tipo_documento, campos, procesado_con_ia y una
    advertencia para el frontend.
    """
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

    try:
        resultado = vision_json(imagen_path, prompt, max_tokens=2048)
    except json.JSONDecodeError:
        resultado = {"tipo_documento": "desconocido", "confianza_lectura": "baja", "campos": {}}

    return {
        "tipo_documento":    resultado.get("tipo_documento", "desconocido"),
        "confianza_lectura": resultado.get("confianza_lectura", "baja"),
        "campos":            resultado.get("campos", {}),
        "procesado_con_ia":  True,
        "advertencia": (
            "Este documento fue procesado con inteligencia artificial "
            "porque estaba en mal estado o era ilegible. "
            "Los datos extraídos pueden contener errores. "
            "Verifica la información manualmente antes de usarla."
        ),
    }
