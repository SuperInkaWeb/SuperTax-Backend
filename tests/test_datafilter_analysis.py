"""
Análisis de DataFilter (Fase 4a): builders seguros + materialización local.

Los tests de los builders son puros (sin BD ni DuckDB). Las pruebas de integración
arman un Parquet local y ejercen columnas calculadas, reemplazos, dedupe y stats
contra el motor, más la materialización a un Parquet derivado.
"""
import os

import pytest

from src.modules.datafilter.domain.analysis import (
    Aggregation,
    MatchMode,
    Measure,
    PivotSpec,
    ReplacementRule,
)
from src.modules.datafilter.domain.expressions import (
    ColumnExpr,
    ComputedColumn,
    FunctionExpr,
    FunctionName,
    LiteralExpr,
)
from src.modules.datafilter.infrastructure import (
    dedupe_builder,
    expression_builder,
    pivot_builder,
    query_builder,
    replace_builder,
)
from src.platform.config.settings import settings

_VALID = {"serie", "igv"}


# ── Builders puros (seguridad A03) ───────────────────────────────────────

def test_build_expression_literal_es_parametro():
    expr = FunctionExpr(
        fn=FunctionName.ADD, args=[ColumnExpr(name="igv"), LiteralExpr(value=10)]
    )
    sql, params = expression_builder.build_expression(expr, _VALID)
    assert sql == '("igv" + ?)'
    assert params == [10]


def test_build_compute_select_rechaza_alias_existente():
    columnas = [ComputedColumn(name="igv", expression=ColumnExpr(name="serie"))]
    with pytest.raises(query_builder.InvalidFilterError):
        expression_builder.build_compute_select(columnas, _VALID)


def test_build_expression_columna_desconocida_rechazada():
    with pytest.raises(query_builder.InvalidFilterError):
        expression_builder.build_expression(ColumnExpr(name="hack"), _VALID)


def test_build_replace_exact_usa_parametros():
    reglas = [ReplacementRule(column="serie", mode=MatchMode.EXACT, search="B001", replace="B999")]
    sql, params = replace_builder.build_replace_select(reglas, ["serie", "igv"], _VALID)
    assert "CASE WHEN" in sql
    assert params == ["B001", "B999"]
    assert '"igv"' in sql  # columna sin reglas se emite tal cual


def test_build_dedupe_sin_claves_y_con_claves():
    assert dedupe_builder.build_dedupe([], _VALID) == ("DISTINCT *", None)
    select_sql, qualify_sql = dedupe_builder.build_dedupe(["serie"], _VALID)
    assert select_sql == "*"
    assert 'PARTITION BY "serie"' in qualify_sql


def test_build_pivot_suma_redondeada_y_group_by():
    spec = PivotSpec(
        group_by=["serie"], measures=[Measure(column="igv", aggregation=Aggregation.SUM)]
    )
    select_sql, group_cols_sql, order_sql, params = pivot_builder.build_pivot(
        spec, _VALID, {"igv"}, None
    )
    assert group_cols_sql == '"serie"'
    assert 'round(sum("igv")' in select_sql
    assert params == [] and order_sql is None


def test_build_pivot_suma_sobre_texto_rechazada():
    spec = PivotSpec(
        group_by=["serie"], measures=[Measure(column="serie", aggregation=Aggregation.SUM)]
    )
    with pytest.raises(query_builder.InvalidFilterError):
        pivot_builder.build_pivot(spec, _VALID, {"igv"}, None)


def test_build_pivot_crosstab_valor_como_parametro():
    spec = PivotSpec(
        group_by=["serie"],
        measures=[Measure(aggregation=Aggregation.COUNT)],
        pivot_column="igv",
    )
    select_sql, _, _, params = pivot_builder.build_pivot(spec, _VALID, {"igv"}, [69.05, 20.12])
    assert "CASE WHEN" in select_sql and "?" in select_sql
    assert params == [69.05, 20.12]  # valores del cross-tab NUNCA concatenados


# ── Integración con el motor (Parquet local) ─────────────────────────────

@pytest.fixture()
def parquet_local(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))
    from src.modules.datafilter.infrastructure import duckdb_engine

    origen = os.path.join(tmp_path, "in.csv")
    with open(origen, "w", encoding="utf-8") as f:
        f.write("serie,igv\nB001,69.05\nB002,20.12\nB001,69.05\n")
    parquet = str(tmp_path / "out.parquet")
    duckdb_engine.convert_to_parquet(origen, parquet, ".csv")
    return parquet, str(tmp_path), duckdb_engine


def test_compute_materializa_columna_calculada(parquet_local):
    parquet, tmp, duckdb_engine = parquet_local
    columnas = [ComputedColumn(
        name="igv_doble",
        expression=FunctionExpr(
            fn=FunctionName.MUL, args=[ColumnExpr(name="igv"), LiteralExpr(value=2)]
        ),
    )]
    select_sql, params = expression_builder.build_compute_select(columnas, {"serie", "igv"})
    dest = os.path.join(tmp, "derivado.parquet")
    duckdb_engine.materialize_to_parquet(parquet, select_sql, "", params, dest)

    cols = {c.name for c in duckdb_engine.get_schema(dest)}
    assert "igv_doble" in cols and {"serie", "igv"} <= cols
    assert duckdb_engine.count_matches(dest, "", []) == 3


def test_dedupe_quita_filas_identicas(parquet_local):
    parquet, _, duckdb_engine = parquet_local
    select_sql, qualify_sql = dedupe_builder.build_dedupe([], {"serie", "igv"})
    total = duckdb_engine.dedupe_count(parquet, select_sql, "", qualify_sql, [])
    assert total == 2  # la fila B001/69.05 estaba repetida


def test_replace_corrige_valor(parquet_local):
    parquet, tmp, duckdb_engine = parquet_local
    reglas = [ReplacementRule(column="serie", mode=MatchMode.EXACT, search="B002", replace="B777")]
    select_sql, params = replace_builder.build_replace_select(
        reglas, ["serie", "igv"], {"serie", "igv"}
    )
    dest = os.path.join(tmp, "corregido.parquet")
    duckdb_engine.materialize_to_parquet(parquet, select_sql, "", params, dest)
    _, rows = duckdb_engine.preview(dest, "*", '"serie" = ?', "", 10, 0, ["B777"])
    assert len(rows) == 1


def test_stats_columna_numerica(parquet_local):
    parquet, _, duckdb_engine = parquet_local
    row = duckdb_engine.column_stats(parquet, '"igv"', "", [], numeric=True)
    total, non_null, distinct = int(row[0]), int(row[1]), int(row[2])
    assert total == 3 and non_null == 3 and distinct == 2


def test_pivot_agrupa_y_suma(parquet_local):
    parquet, _, duckdb_engine = parquet_local
    spec = PivotSpec(
        group_by=["serie"], measures=[Measure(column="igv", aggregation=Aggregation.SUM)]
    )
    select_sql, group_cols_sql, order_sql, params = pivot_builder.build_pivot(
        spec, {"serie", "igv"}, {"igv"}, None
    )
    assert duckdb_engine.pivot_count(parquet, group_cols_sql, "", []) == 2  # B001, B002
    _, rows = duckdb_engine.pivot_preview(
        parquet, select_sql, group_cols_sql, "", params, 10, 0, order_sql
    )
    por_serie = {r["serie"]: r["suma_igv"] for r in rows}
    assert por_serie["B001"] == 138.1  # 69.05 + 69.05
    assert por_serie["B002"] == 20.12


def test_pivot_materializa_a_parquet(parquet_local):
    parquet, tmp, duckdb_engine = parquet_local
    spec = PivotSpec(
        group_by=["serie"], measures=[Measure(column="igv", aggregation=Aggregation.SUM)]
    )
    select_sql, group_cols_sql, order_sql, params = pivot_builder.build_pivot(
        spec, {"serie", "igv"}, {"igv"}, None
    )
    dest = os.path.join(tmp, "reporte.parquet")
    duckdb_engine.pivot_to_parquet(parquet, select_sql, group_cols_sql, "", params, dest, order_sql)
    cols = {c.name for c in duckdb_engine.get_schema(dest)}
    assert cols == {"serie", "suma_igv"}
    assert duckdb_engine.count_matches(dest, "", []) == 2
