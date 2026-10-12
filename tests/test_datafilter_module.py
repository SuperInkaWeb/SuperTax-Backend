"""
Andamiaje del módulo DataFilter (Fase 1).

Smoke tests sin BD: el módulo está registrado y su router se monta bajo
/api/datafilter. La ingesta/filtrado llegan en fases siguientes.
"""
from src.main import create_app
from src.module_registry import MODULES


def test_datafilter_registrado_en_modulos():
    keys = [d.key for d in MODULES]
    assert "datafilter" in keys


def test_permisos_datafilter_expuestos():
    df = next(d for d in MODULES if d.key == "datafilter")
    assert "datafilter.dataset.read" in df.permissions
    assert "datafilter.dataset.create" in df.permissions


def test_ruta_listar_datasets_montada():
    paths = create_app().openapi()["paths"]
    assert "/api/datafilter/datasets" in paths
