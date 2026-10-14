"""Escaneo + ingesta (Fase 3): validador de adjuntos, query del buzón, rutas. Sin red."""
from datetime import date

from src.modules.correos.infrastructure import mail_client, validator

_PDF = b"%PDF-1.4\n" + b"%\xe2\xe3\xcf\xd3\n" + b"1 0 obj\n" * 5 + b"trailer\n" * 10
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def test_validar_pdf_ok():
    res = validator.validar(_PDF, nombre="recibo.pdf", tamano_maximo=10_000_000)
    assert res.ok and res.mime == "application/pdf" and len(res.sha256) == 64


def test_validar_extension_no_coincide():
    # Contenido PNG con nombre .pdf: se detecta por contenido y se rechaza.
    res = validator.validar(_PNG, nombre="foto.pdf", tamano_maximo=10_000_000)
    assert not res.ok and res.motivo == "extension_no_coincide_con_contenido"


def test_validar_tipo_no_reconocido():
    res = validator.validar(b"no soy un documento valido" * 4, nombre="x.pdf", tamano_maximo=10_000)
    assert not res.ok and res.motivo == "tipo_no_reconocido"


def test_validar_truncado():
    res = validator.validar(b"%PDF-", nombre="x.pdf", tamano_maximo=10_000)
    assert not res.ok and res.motivo == "archivo_vacio_o_truncado"


def test_validar_pdf_con_javascript_rechazado():
    hostil = _PDF + b"/JavaScript (app.alert)"
    res = validator.validar(hostil, nombre="malo.pdf", tamano_maximo=10_000_000)
    assert not res.ok and res.motivo == "pdf_con_contenido_activo"


def test_gmail_consulta():
    consulta = mail_client.GmailClient._consulta(date(2026, 1, 1), date(2026, 2, 1), "INBOX")
    assert consulta == "has:attachment in:inbox after:2026/01/01 before:2026/02/01"


def test_cliente_de_proveedor():
    assert isinstance(mail_client.cliente_de("gmail"), mail_client.GmailClient)
    assert isinstance(mail_client.cliente_de("outlook"), mail_client.GraphClient)


def test_rutas_scan_montadas():
    from src.main import create_app

    paths = create_app().openapi()["paths"]
    assert "/api/correos/scans" in paths
    assert "/api/correos/scans/{job_id}/cancel" in paths
    assert "/api/correos/scans/{job_id}/errors" in paths
    assert "/api/correos/scans/{job_id}/stream" in paths
