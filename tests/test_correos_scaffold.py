"""Andamiaje del módulo Correos (Fase 1): registro, rutas y permisos. Sin BD."""
from src.main import create_app
from src.module_registry import MODULES


def test_modulo_correos_registrado():
    claves = {m.key for m in MODULES}
    assert "correos" in claves


def test_ruta_mailboxes_montada():
    app = create_app()
    paths = app.openapi()["paths"]
    assert "/api/correos/mailboxes" in paths


def test_permisos_declarados():
    correos = next(m for m in MODULES if m.key == "correos")
    assert "correos.mailbox.read" in correos.permissions
    assert "correos.scan.create" in correos.permissions
    assert "correos.record.update" in correos.permissions


def test_modelos_en_metadata():
    # models_registry debe registrar las tablas del schema correos en la metadata.
    import src.models_registry  # noqa: F401
    from src.platform.database.base import Base

    tablas = {t.fullname for t in Base.metadata.tables.values()}
    assert "correos.mailbox_connections" in tablas
    assert "correos.extracted_records" in tablas
    assert "correos.scan_jobs" in tablas
