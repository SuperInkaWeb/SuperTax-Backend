"""
Filtrado de DataFilter (Fase 3): query-builder seguro + preview/export.

Los tests del query-builder son puros (sin BD ni DuckDB). La prueba de
integración arma un Parquet local y ejerce filtro → preview → export.
"""
import os

import pytest

from src.modules.datafilter.domain.filtering import (
    Combinator,
    FilterGroup,
    FilterLeaf,
    Operator,
    SortDirection,
    SortSpec,
)
from src.modules.datafilter.infrastructure import query_builder
from src.platform.config.settings import settings

_VALID = {"serie", "igv"}


def _grupo(*hojas) -> FilterGroup:
    return FilterGroup(combinator=Combinator.AND, children=list(hojas))


def test_build_where_eq_usa_parametros():
    where, params = query_builder.build_where(
        _grupo(FilterLeaf(column="serie", operator=Operator.EQ, value="B001")), _VALID
    )
    assert where == '("serie" = ?)'
    assert params == ["B001"]


def test_build_where_columna_desconocida_rechazada():
    with pytest.raises(query_builder.InvalidFilterError):
        query_builder.build_where(
            _grupo(FilterLeaf(column="hack", operator=Operator.EQ, value=1)), _VALID
        )


def test_in_y_between():
    where, params = query_builder.build_where(
        _grupo(FilterLeaf(column="igv", operator=Operator.IN, value=[1, 2, 3])), _VALID
    )
    assert "IN (?, ?, ?)" in where and params == [1, 2, 3]
    where, params = query_builder.build_where(
        _grupo(FilterLeaf(column="igv", operator=Operator.BETWEEN, value=[1, 10])), _VALID
    )
    assert "BETWEEN ? AND ?" in where and params == [1, 10]


def test_select_y_order_by():
    assert query_builder.build_select(["serie"], _VALID) == '"serie"'
    assert query_builder.build_select([], _VALID) == "*"
    order = query_builder.build_order_by(
        [SortSpec(column="igv", direction=SortDirection.DESC)], _VALID
    )
    assert order == 'ORDER BY "igv" DESC'


def test_filtro_preview_y_export(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))
    from src.modules.datafilter.infrastructure import duckdb_engine

    origen = os.path.join(tmp_path, "in.csv")
    with open(origen, "w", encoding="utf-8") as f:
        f.write("serie,igv\nB001,69.05\nB002,20.12\nB001,10.00\n")
    parquet = str(tmp_path / "out.parquet")
    duckdb_engine.convert_to_parquet(origen, parquet, ".csv")

    where, params = query_builder.build_where(
        _grupo(FilterLeaf(column="serie", operator=Operator.EQ, value="B001")), _VALID
    )
    _, rows = duckdb_engine.preview(parquet, "*", where, "", 100, 0, params)
    assert len(rows) == 2
    assert duckdb_engine.count_matches(parquet, where, params) == 2

    destino = str(tmp_path / "export.csv")
    duckdb_engine.export_to_file(parquet, "*", where, "", params, destino, "csv", None)
    with open(destino, encoding="utf-8") as f:
        contenido = f.read()
    assert contenido.count("B001") == 2
    assert "B002" not in contenido
