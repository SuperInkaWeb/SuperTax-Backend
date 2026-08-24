"""
Extrae datos de los XML (UBL 2.1) de los comprobantes descargados para armar una
descripción legible por comprobante y enriquecer los resultados del job.

El XML es la fuente estructurada del comprobante (el PDF es su representación
visual): de él salen emisor, RUC, tipo, monto, moneda, fecha y el concepto de los
ítems. El enriquecimiento es best-effort: nunca rompe el job.
"""
import logging
import os
import xml.etree.ElementTree as ET
import zipfile

_log = logging.getLogger("sunat.comprobante_xml")

_NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}

_EMISOR = "cac:AccountingSupplierParty/cac:Party"

TIPOS = {
    "01": "Factura",
    "03": "Boleta",
    "07": "Nota de crédito",
    "08": "Nota de débito",
    "09": "Guía de remisión",
}


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _leer_xml(path: str) -> bytes | None:
    """Devuelve el XML del comprobante, venga como .xml o dentro de un .zip."""
    low = path.lower()
    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                for nombre in z.namelist():
                    if nombre.lower().endswith(".xml"):
                        return z.read(nombre)
            return None
        if low.endswith(".xml"):
            with open(path, "rb") as f:
                return f.read()
    except Exception:
        return None
    return None


def parse_comprobante(xml_bytes: bytes) -> dict | None:
    """Parsea un XML UBL de comprobante. Devuelve dict con id/emisor/ruc/tipo/
    monto/moneda/fecha/concepto/descripcion, o None si no es un comprobante UBL."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    tag = root.tag.rsplit("}", 1)[-1]  # Invoice / CreditNote / DebitNote
    cid = _text(root.find("cbc:ID", _NS))
    if not cid:
        return None

    if tag == "CreditNote":
        tipo = "07"
    elif tag == "DebitNote":
        tipo = "08"
    else:
        tipo = _text(root.find("cbc:InvoiceTypeCode", _NS)) or "01"

    emisor = _text(root.find(f"{_EMISOR}/cac:PartyLegalEntity/cbc:RegistrationName", _NS)) or _text(
        root.find(f"{_EMISOR}/cac:PartyName/cbc:Name", _NS)
    )
    ruc = _text(root.find(f"{_EMISOR}/cac:PartyIdentification/cbc:ID", _NS))

    total_el = root.find("cac:LegalMonetaryTotal/cbc:PayableAmount", _NS)
    monto = _text(total_el)
    moneda = total_el.get("currencyID", "") if total_el is not None else ""
    fecha = _text(root.find("cbc:IssueDate", _NS))

    descripciones = [_text(d) for d in root.findall(".//cac:Item/cbc:Description", _NS) if _text(d)]
    concepto = "; ".join(descripciones[:2]) + ("…" if len(descripciones) > 2 else "")

    tipo_txt = TIPOS.get(tipo, f"Comprobante {tipo}")
    partes = [f"{tipo_txt} de {emisor}" if emisor else tipo_txt]
    if concepto:
        partes.append(concepto)
    if monto:
        partes.append(f"{moneda} {monto}".strip())
    descripcion = " — ".join(partes)

    return {
        "id": cid, "emisor": emisor, "ruc": ruc, "tipo": tipo,
        "monto": monto, "moneda": moneda, "fecha": fecha,
        "concepto": concepto, "descripcion": descripcion,
    }


def _norm(cid) -> str:
    return str(cid).strip().upper()


def enriquecer_resultados(resultados: list[dict], job_dir: str) -> list[dict]:
    """Recorre los XML del job y agrega descripción/emisor/monto/fecha a cada
    resultado (emparejando por serie-número). Best-effort: no rompe el job."""
    por_id: dict[str, dict] = {}
    try:
        for base, _dirs, files in os.walk(job_dir):
            for fn in files:
                xml = _leer_xml(os.path.join(base, fn))
                if xml is None:
                    continue
                info = parse_comprobante(xml)
                if info and info.get("id"):
                    por_id[_norm(info["id"])] = info
    except Exception:
        _log.warning("No se pudieron leer los XML del job para la descripción", exc_info=True)

    campos = ("emisor", "ruc", "tipo", "monto", "moneda", "fecha", "concepto", "descripcion")
    for r in resultados:
        info = por_id.get(_norm(r.get("id", "")))
        if info:
            for k in campos:
                if info.get(k):
                    r[k] = info[k]
        else:
            r.setdefault("descripcion", "Sin XML — descripción no disponible")
    return resultados
