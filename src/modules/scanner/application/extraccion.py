"""
Orquestación de la extracción de documentos (adaptada del proyecto Scanner).

Pipeline: validar → guardar temp → OCR/PDF/Excel → clasificar → (fallback OCR
rasterizado → fallback IA) → extraer campos → guardar archivo en el storage de la
plataforma + registro en la BD (schema `scanner`).

El motor de OCR/extracción se importa perezosamente: el API arranca sin requerir
el binario de Tesseract ni las librerías de imagen.
"""
import logging
import os
import tempfile
import uuid

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from src.modules.scanner.infrastructure.config import (
    EXTENSIONES_VALIDAS,
    TAMANO_MAXIMO,
)
from src.modules.scanner.infrastructure.repositories import SqlDocumentoRepository
from src.platform.storage.base import FileStorage

logger = logging.getLogger("scanner.extraccion")


def _guardar_temp(file: UploadFile) -> str:
    ext = os.path.splitext(file.filename or "")[1].lower()
    contenido = file.file.read()
    if len(contenido) > TAMANO_MAXIMO:
        raise HTTPException(400, detail="El archivo supera el límite de 10 MB")
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(contenido)
        return tmp.name


def _limpiar(path: str | None) -> None:
    if path and os.path.exists(path):
        os.remove(path)


def _extractores():
    """Mapa tipo→extractor (import perezoso del motor de extracción)."""
    from src.modules.scanner.infrastructure.extractor.asistencia import extract_asistencia
    from src.modules.scanner.infrastructure.extractor.comprobante import extract_comprobante
    from src.modules.scanner.infrastructure.extractor.recibo_agua import extract_recibo_agua
    from src.modules.scanner.infrastructure.extractor.recibo_gas import extract_recibo_gas
    from src.modules.scanner.infrastructure.extractor.recibo_luz import extract_recibo_luz
    from src.modules.scanner.infrastructure.extractor.recibo_telefonia import (
        extract_recibo_telefonia,
    )

    return {
        "factura_electronica": lambda p: extract_comprobante(p, "factura_electronica"),
        "boleta_venta": lambda p: extract_comprobante(p, "boleta_venta"),
        "recibo_honorarios": lambda p: extract_comprobante(p, "recibo_honorarios"),
        "nota_credito": lambda p: extract_comprobante(p, "nota_credito"),
        "nota_debito": lambda p: extract_comprobante(p, "nota_debito"),
        "recibo_luz": extract_recibo_luz,
        "recibo_agua": extract_recibo_agua,
        "recibo_gas": extract_recibo_gas,
        "recibo_telefonia": extract_recibo_telefonia,
        "asistencia": extract_asistencia,
    }


async def procesar_documento(
    db: Session, storage: FileStorage, company_id: int, user_id: int, file: UploadFile
) -> dict:
    import fitz
    import openpyxl

    from src.modules.scanner.infrastructure.extractor.clasificador import (
        TIPO_DESCONOCIDO,
        clasificar,
        etiqueta,
    )
    from src.modules.scanner.infrastructure.extractor.ia_fallback import ia_leer_documento
    from src.modules.scanner.infrastructure.utils import (
        EXTENSIONES_IMAGEN,
        imagen_a_texto,
        limpiar_texto,
        pdf_a_texto,
    )

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in EXTENSIONES_VALIDAS:
        raise HTTPException(
            400,
            detail=(
                f"Formato '{ext}' no soportado. "
                f"Válidos: {', '.join(sorted(EXTENSIONES_VALIDAS))}"
            ),
        )

    temp_path: str | None = None
    tmp_img_ocr: str | None = None
    tmp_img_ia: str | None = None
    try:
        temp_path = _guardar_temp(file)

        # 1. Extraer texto
        if ext in EXTENSIONES_IMAGEN:
            texto_raw = imagen_a_texto(temp_path)
        elif ext == ".xlsx":
            wb = openpyxl.load_workbook(temp_path, data_only=True)
            filas = list(wb.active.iter_rows(values_only=True))
            texto_raw = " ".join(
                str(c) for row in filas[:5] for c in row if c is not None
            )
        else:
            texto_raw = pdf_a_texto(temp_path)
        texto_limpio = limpiar_texto(texto_raw)

        # 2. Clasificar
        tipo, confianza = clasificar(texto_limpio)

        # 3. Fallback OCR para PDF escaneado sin texto
        if (
            tipo == TIPO_DESCONOCIDO
            and ext not in EXTENSIONES_IMAGEN
            and ext != ".xlsx"
            and len([c for c in texto_limpio if c.isalpha()]) < 400
        ):
            doc = fitz.open(temp_path)
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(3, 3))
            doc.close()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_img_ocr = tmp.name
            pix.save(tmp_img_ocr)
            texto_limpio = limpiar_texto(imagen_a_texto(tmp_img_ocr))
            tipo, confianza = clasificar(texto_limpio)

        # 4. Fallback IA
        resultado_ia = None
        if tipo == TIPO_DESCONOCIDO:
            try:
                img_para_ia = tmp_img_ocr or (temp_path if ext in EXTENSIONES_IMAGEN else None)
                if not img_para_ia:
                    doc = fitz.open(temp_path)
                    pix = doc[0].get_pixmap(matrix=fitz.Matrix(3, 3))
                    doc.close()
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        tmp_img_ia = tmp.name
                    pix.save(tmp_img_ia)
                    img_para_ia = tmp_img_ia
                resultado_ia = ia_leer_documento(img_para_ia)
                tipo = resultado_ia["tipo_documento"]
                confianza = 0.0
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(
                    422,
                    detail=(
                        "No se pudo determinar el tipo de documento. Verifica que "
                        "sea un comprobante o planilla peruano legible."
                    ),
                )

        if tipo == TIPO_DESCONOCIDO:
            raise HTTPException(422, detail="No se pudo determinar el tipo de documento.")

        # 5. Extraer campos
        if resultado_ia:
            campos = resultado_ia["campos"]
            campos["procesado_con_ia"] = True
            campos["advertencia"] = resultado_ia["advertencia"]
            campos["confianza_lectura"] = resultado_ia["confianza_lectura"]
        else:
            extractor = _extractores().get(tipo)
            if not extractor:
                raise HTTPException(422, detail=f"Tipo '{tipo}' no tiene extractor disponible.")
            campos_raw = extractor(temp_path)
            if isinstance(campos_raw, list):
                if not campos_raw:
                    raise HTTPException(422, detail="No se encontraron registros en la planilla.")
                campos = {"registros": campos_raw, "total_registros": len(campos_raw)}
            else:
                campos = campos_raw
                campos["tipo_comprobante"] = tipo

        # 6. Guardar archivo (storage plataforma) + registro (BD)
        nombre = file.filename or "archivo"
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in nombre)
        storage_path = f"scanner/documentos/{company_id}/{uuid.uuid4().hex}_{safe}"
        with open(temp_path, "rb") as f:
            storage.save(storage_path, f.read())

        doc = SqlDocumentoRepository(db).create(
            company_id=company_id,
            user_id=user_id,
            tipo_documento=tipo,
            tipo_etiqueta=etiqueta(tipo),
            confianza=confianza,
            nombre_archivo=file.filename or "archivo",
            storage_path=storage_path,
            campos=campos,
        )
        return {
            "status": "success",
            "id": doc.id,
            "tipo_documento": tipo,
            "tipo_etiqueta": etiqueta(tipo),
            "confianza": confianza,
            "campos": campos,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error procesando documento %s", file.filename)
        raise HTTPException(500, detail="Error al procesar el documento. Intenta de nuevo.")
    finally:
        _limpiar(temp_path)
        _limpiar(tmp_img_ocr)
        _limpiar(tmp_img_ia)
