"""
Construcción SEGURA de expresiones para columnas calculadas (defensa A03).

Traduce el árbol de expresiones (domain.expressions) a SQL de DuckDB:
  - Las columnas se validan contra el esquema real (whitelist).
  - Los literales van SIEMPRE como parámetros ('?').
  - Las funciones provienen de un enum CERRADO; cada una tiene una plantilla fija
    y una aridad validada. Nada del usuario se interpola como código.

`build_expression` devuelve (fragmento_sql, params). `build_compute_select` arma el
'SELECT *, expr AS "alias", ...' completo validando nombres y expresiones.
"""
from src.modules.datafilter.domain.expressions import (
    ColumnExpr,
    ComputedColumn,
    Expression,
    FunctionExpr,
    FunctionName,
    LiteralExpr,
)
from src.modules.datafilter.infrastructure.query_builder import (
    InvalidFilterError,
    quote_column,
)

_MAX_DEPTH = 8    # niveles de anidamiento permitidos
_MAX_NODES = 100  # nodos totales por expresión

# Aridad permitida por función: (mínimo, máximo | None si es variádica).
_ARITY: dict[FunctionName, tuple[int, int | None]] = {
    FunctionName.CONCAT: (2, None),
    FunctionName.UPPER: (1, 1),
    FunctionName.LOWER: (1, 1),
    FunctionName.TRIM: (1, 1),
    FunctionName.LENGTH: (1, 1),
    FunctionName.SUBSTR: (2, 3),
    FunctionName.REPLACE: (3, 3),
    FunctionName.ADD: (2, 2),
    FunctionName.SUB: (2, 2),
    FunctionName.MUL: (2, 2),
    FunctionName.DIV: (2, 2),
    FunctionName.ROUND: (1, 2),
    FunctionName.IF: (3, 3),
    FunctionName.EQ: (2, 2),
    FunctionName.NE: (2, 2),
    FunctionName.GT: (2, 2),
    FunctionName.GTE: (2, 2),
    FunctionName.LT: (2, 2),
    FunctionName.LTE: (2, 2),
    FunctionName.AND: (2, None),
    FunctionName.OR: (2, None),
    FunctionName.NOT: (1, 1),
    FunctionName.YEAR: (1, 1),
    FunctionName.MONTH: (1, 1),
    FunctionName.DAY: (1, 1),
    FunctionName.DATEDIFF_DAYS: (2, 2),
}

# Operadores binarios: función -> símbolo SQL.
_BINARY_OP: dict[FunctionName, str] = {
    FunctionName.ADD: "+",
    FunctionName.SUB: "-",
    FunctionName.MUL: "*",
    FunctionName.EQ: "=",
    FunctionName.NE: "!=",
    FunctionName.GT: ">",
    FunctionName.GTE: ">=",
    FunctionName.LT: "<",
    FunctionName.LTE: "<=",
}

# Funciones escalares simples: función -> nombre SQL (aplicación directa a los args).
_SCALAR_FN: dict[FunctionName, str] = {
    FunctionName.CONCAT: "concat",
    FunctionName.UPPER: "upper",
    FunctionName.LOWER: "lower",
    FunctionName.TRIM: "trim",
    FunctionName.LENGTH: "length",
    FunctionName.SUBSTR: "substr",
    FunctionName.REPLACE: "replace",
    FunctionName.ROUND: "round",
}

# Extracción de parte de fecha: función -> parte (se envuelve con try_cast a DATE).
_DATE_PART: dict[FunctionName, str] = {
    FunctionName.YEAR: "year",
    FunctionName.MONTH: "month",
    FunctionName.DAY: "day",
}


def build_expression(expr: Expression, valid_columns: set[str]) -> tuple[str, list]:
    """Traduce una expresión a (sql, params). Punto de entrada (aplica límites)."""
    _assert_limits(expr)
    return _build(expr, valid_columns)


def build_compute_select(
    columns: list[ComputedColumn], valid_columns: set[str]
) -> tuple[str, list]:
    """Arma 'SELECT *, expr AS "alias", ...' validando nombres y expresiones."""
    pieces = ["*"]
    params: list = []
    seen: set[str] = set()
    for column in columns:
        alias = column.name.strip()
        if not alias:
            raise InvalidFilterError("El nombre de la columna calculada no puede estar vacío.")
        if alias in valid_columns:
            raise InvalidFilterError(f"Ya existe una columna llamada '{alias}'.")
        if alias in seen:
            raise InvalidFilterError(f"Nombre de columna calculada repetido: '{alias}'.")
        seen.add(alias)
        expr_sql, expr_params = build_expression(column.expression, valid_columns)
        pieces.append(f"{expr_sql} AS {_quote_alias(alias)}")
        params.extend(expr_params)
    return ", ".join(pieces), params


def _quote_alias(alias: str) -> str:
    """Cita un alias de salida como identificador SQL seguro (escapa comillas dobles)."""
    return '"' + alias.replace('"', '""') + '"'


def _build(expr: Expression, valid_columns: set[str]) -> tuple[str, list]:
    if isinstance(expr, ColumnExpr):
        return quote_column(expr.name, valid_columns), []
    if isinstance(expr, LiteralExpr):
        return "?", [expr.value]
    if isinstance(expr, FunctionExpr):
        return _build_function(expr, valid_columns)
    raise InvalidFilterError("Nodo de expresión no reconocido.")


def _build_function(expr: FunctionExpr, valid_columns: set[str]) -> tuple[str, list]:
    _check_arity(expr)
    args_sql: list[str] = []
    params: list = []
    for child in expr.args:
        child_sql, child_params = _build(child, valid_columns)
        args_sql.append(child_sql)
        params.extend(child_params)
    return _compose(expr.fn, args_sql), params


def _compose(fn: FunctionName, args: list[str]) -> str:
    """Ensambla el SQL de la función a partir de sus argumentos ya construidos."""
    if fn is FunctionName.DIV:
        # División segura: NULL en vez de error cuando el divisor es 0.
        return f"({args[0]} / NULLIF({args[1]}, 0))"
    if fn in _BINARY_OP:
        return f"({args[0]} {_BINARY_OP[fn]} {args[1]})"
    if fn in _SCALAR_FN:
        return f"{_SCALAR_FN[fn]}({', '.join(args)})"
    if fn is FunctionName.AND:
        return "(" + " AND ".join(args) + ")"
    if fn is FunctionName.OR:
        return "(" + " OR ".join(args) + ")"
    if fn is FunctionName.NOT:
        return f"(NOT {args[0]})"
    if fn is FunctionName.IF:
        return f"CASE WHEN {args[0]} THEN {args[1]} ELSE {args[2]} END"
    if fn in _DATE_PART:
        return f"{_DATE_PART[fn]}(try_cast({args[0]} AS DATE))"
    if fn is FunctionName.DATEDIFF_DAYS:
        return (
            f"date_diff('day', try_cast({args[0]} AS DATE), "
            f"try_cast({args[1]} AS DATE))"
        )
    raise InvalidFilterError(f"Función no soportada: {fn!r}")


def _check_arity(expr: FunctionExpr) -> None:
    minimum, maximum = _ARITY[expr.fn]
    count = len(expr.args)
    if count < minimum or (maximum is not None and count > maximum):
        if maximum == minimum:
            expected = f"{minimum}"
        elif maximum is None:
            expected = f"{minimum}+"
        else:
            expected = f"{minimum}-{maximum}"
        raise InvalidFilterError(
            f"La función '{expr.fn.value}' espera {expected} argumento(s), recibió {count}."
        )


def _assert_limits(expr: Expression) -> None:
    depth, count = _measure(expr)
    if depth > _MAX_DEPTH:
        raise InvalidFilterError(f"La expresión está demasiado anidada (máximo {_MAX_DEPTH}).")
    if count > _MAX_NODES:
        raise InvalidFilterError(f"La expresión tiene demasiados nodos (máximo {_MAX_NODES}).")


def _measure(expr: Expression, depth: int = 1) -> tuple[int, int]:
    if not isinstance(expr, FunctionExpr) or not expr.args:
        return depth, 1
    max_depth = depth
    total = 1
    for child in expr.args:
        child_depth, child_count = _measure(child, depth + 1)
        max_depth = max(max_depth, child_depth)
        total += child_count
    return max_depth, total
