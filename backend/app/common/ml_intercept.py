"""Plan SQL prediction projections and recognize standalone forecast statements."""

import re
from dataclasses import dataclass

from app.modules.ml_engine.spec import UnsupportedMLSQLExpression
from app.modules.query.dialect.parser import _parse_tree, _walk_nodes


@dataclass(frozen=True)
class MLPredictCall:
    start_index: int
    end_index: int
    alias: str
    feature_args: tuple[str, ...]

    def start(self) -> int:
        return self.start_index

    def end(self) -> int:
        return self.end_index

    def group(self, index: int) -> str:
        if index == 1:
            return self.alias
        if index == 2:
            return ", ".join(self.feature_args)
        raise IndexError(index)


@dataclass(frozen=True)
class MLPredictRewrite:
    alias: str
    feature_sql: str
    feature_columns: tuple[str, ...]
    prediction_index: int
    prediction_name: str
    predictions: tuple["MLPredictExpression", ...] = ()


@dataclass(frozen=True)
class MLPredictExpression:
    alias: str
    feature_args: tuple[str, ...]
    feature_columns: tuple[str, ...]
    prediction_index: int
    prediction_name: str


@dataclass(frozen=True)
class MLForecastCall:
    model_alias: str | None
    model_id: str | None
    version: int | None
    horizon: int
    series: str | None = None
    confidence_level: int = 95


def detect_ml_forecast(sql: str) -> MLForecastCall | None:
    """Parse Nova's table-oriented persisted forecast statement.

    Supported forms are::

        SELECT * FROM ML_FORECAST(MODEL => 'alias', HORIZON => 30)
        SELECT * FROM ML_FORECAST(
            MODEL_ID => 'id', VERSION => 2, HORIZON => 30,
            SERIES => 'west', CONFIDENCE => 90
        )

    The argument scanner is balanced and quote-aware; forecast is deliberately
    separate from scalar ``ML_PREDICT`` because it creates future rows.
    """
    prefix = re.match(r"\s*SELECT\s+\*\s+FROM\s+ML_FORECAST\b", sql, re.IGNORECASE)
    if prefix is None:
        return None
    opening = prefix.end()
    while opening < len(sql) and sql[opening].isspace():
        opening += 1
    if opening >= len(sql) or sql[opening] != "(":
        raise ValueError("ML_FORECAST requires named arguments in parentheses")
    closing = _matching_parenthesis(sql, opening, function_name="ML_FORECAST")
    if sql[closing + 1 :].strip().rstrip(";").strip():
        raise ValueError("ML_FORECAST must be the complete table expression")

    values: dict[str, str] = {}
    for argument in _split_args(sql[opening + 1 : closing]):
        name, separator, value = argument.partition("=>")
        key = name.strip().upper()
        if not separator or not key or not value.strip():
            raise ValueError("ML_FORECAST arguments must use NAME => VALUE syntax")
        if key in values:
            raise ValueError(f"ML_FORECAST argument {key} was provided more than once")
        values[key] = value.strip()
    allowed = {"MODEL", "MODEL_ID", "VERSION", "HORIZON", "SERIES", "CONFIDENCE"}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"Unsupported ML_FORECAST argument: {unknown[0]}")
    if "HORIZON" not in values:
        raise ValueError("ML_FORECAST requires HORIZON")
    model_alias = _sql_string_literal(values["MODEL"], "MODEL") if "MODEL" in values else None
    model_id = _sql_string_literal(values["MODEL_ID"], "MODEL_ID") if "MODEL_ID" in values else None
    if (model_alias is None) == (model_id is None):
        raise ValueError("ML_FORECAST requires exactly one of MODEL or MODEL_ID")
    version = _positive_integer(values["VERSION"], "VERSION") if "VERSION" in values else None
    if model_id is not None and version is None:
        raise ValueError("ML_FORECAST with MODEL_ID requires VERSION")
    if model_alias is not None and version is not None:
        raise ValueError("ML_FORECAST VERSION is only valid with MODEL_ID")
    horizon = _positive_integer(values["HORIZON"], "HORIZON")
    confidence = (
        _positive_integer(values["CONFIDENCE"], "CONFIDENCE") if "CONFIDENCE" in values else 95
    )
    if confidence > 99:
        raise ValueError("ML_FORECAST CONFIDENCE must be between 1 and 99")
    series = _sql_string_literal(values["SERIES"], "SERIES") if "SERIES" in values else None
    return MLForecastCall(
        model_alias=model_alias,
        model_id=model_id,
        version=version,
        horizon=horizon,
        series=series,
        confidence_level=confidence,
    )


def detect_ml_predict(sql: str) -> MLPredictCall | None:
    """Return the first parser-proven ``ML_PREDICT`` expression."""
    if "ml_predict" not in sql.casefold():
        return None
    calls = _ml_predict_nodes(sql)
    if not calls:
        return None
    node = calls[0]
    arguments = list(node.expression())
    if len(arguments) < 2:
        raise ValueError("ML_PREDICT requires an alias and at least one feature")
    alias_token = _source(sql, arguments[0]).strip()
    if not (
        arguments[0].start.tokenIndex == arguments[0].stop.tokenIndex
        and len(alias_token) >= 2
        and alias_token[0] == alias_token[-1] == "'"
        and "\\" not in alias_token
    ):
        raise ValueError("ML_PREDICT model alias must be a string literal")
    return MLPredictCall(
        node.start.start,
        node.stop.stop + 1,
        alias_token[1:-1].replace("''", "'"),
        tuple(_source(sql, argument) for argument in arguments[1:]),
    )


def detect_ml_predict_table(sql: str) -> tuple[str, str] | None:
    if "ml_predict_table" not in sql.casefold():
        return None
    stream, tree, errors = _parse_tree(sql)
    calls = [
        node
        for node in _walk_nodes(tree)
        if type(node).__name__ == "TableFunctionContext"
        and node.qualifiedName().getText().strip("`").casefold() == "ml_predict_table"
    ]
    if not calls:
        if errors and any(
            token.channel == 0 and token.text.casefold().strip("`") == "ml_predict_table"
            for token in stream.tokens
            if token.text
        ):
            raise UnsupportedMLSQLExpression("Cannot safely parse ML_PREDICT_TABLE")
        return None
    if errors or len(calls) != 1:
        raise UnsupportedMLSQLExpression("ML_PREDICT_TABLE requires one complete table call")
    call = calls[0]
    from app.modules.query.dialect.parser import _first_statement

    if (
        len(tree.singleStatement()) != 1
        or type(_first_statement(tree)).__name__ != "QueryStatementContext"
    ):
        raise UnsupportedMLSQLExpression("ML_PREDICT_TABLE requires one SELECT statement")
    specs = [
        node for node in _walk_nodes(tree) if type(node).__name__ == "QuerySpecificationContext"
    ]
    if (
        len(specs) != 1
        or specs[0].getText().casefold() != ("SELECT*FROM" + call.getText()).casefold()
    ):
        raise UnsupportedMLSQLExpression(
            "Use SELECT * FROM ML_PREDICT_TABLE('alias', 'input SQL') without outer clauses"
        )
    if any(
        type(node).__name__
        in {
            "SortItemContext",
            "LimitElementContext",
            "WithClauseContext",
            "SetOperationContext",
            "OutfileContext",
        }
        for node in _walk_nodes(tree)
    ):
        raise UnsupportedMLSQLExpression("ML_PREDICT_TABLE does not support outer query clauses")
    args = call.expressionList().expression() if call.expressionList() else []
    if len(args) != 2:
        raise UnsupportedMLSQLExpression("ML_PREDICT_TABLE requires an alias and input SQL string")
    values = []
    for argument in args:
        text = _source(sql, argument)
        if (
            argument.start.tokenIndex != argument.stop.tokenIndex
            or len(text) < 2
            or text[0] != "'"
            or text[-1] != "'"
            or "\\" in text
        ):
            raise UnsupportedMLSQLExpression("ML_PREDICT_TABLE arguments must be string literals")
        values.append(text[1:-1].replace("''", "'"))
    return values[0], values[1]


def rewrite_ml_predict_projection(sql: str, match: MLPredictCall) -> MLPredictRewrite:
    """Plan all top-level prediction expressions from the StarRocks AST."""
    del match
    _, tree, errors = _parse_tree(sql)
    if errors:
        raise ValueError(str(errors[0]))
    query_specs = [
        node for node in _walk_nodes(tree) if type(node).__name__ == "QuerySpecificationContext"
    ]
    if not query_specs:
        raise UnsupportedMLSQLExpression("ML_PREDICT interception requires a SELECT query")
    statements = tree.singleStatement()
    if len(statements) != 1 or not any(
        type(node).__name__ == "QueryStatementContext"
        for node in getattr(statements[0], "children", ())
    ):
        # StatementContext adds a wrapper in some generated parser versions.
        from app.modules.query.dialect.parser import _first_statement

        if len(statements) != 1 or type(_first_statement(tree)).__name__ != "QueryStatementContext":
            raise UnsupportedMLSQLExpression("ML_PREDICT requires one SELECT statement")
    outer = max(query_specs, key=lambda node: (node.stop.stop, node.stop.stop - node.start.start))
    unsupported = {"SetOperationContext", "SortItemContext", "OutfileContext"}
    if any(type(node).__name__ in unsupported for node in _walk_nodes(tree)):
        raise UnsupportedMLSQLExpression(
            "ML_PREDICT does not support set operations, ORDER BY, or OUTFILE"
        )
    if outer.setQuantifier() is not None or outer.groupingElement() is not None or outer.having:
        raise UnsupportedMLSQLExpression(
            "ML_PREDICT does not support DISTINCT, GROUP BY, or HAVING"
        )
    items = list(outer.selectItem())
    from_clause = outer.fromClause()
    if from_clause is None or from_clause.start is None:
        raise ValueError("ML_PREDICT requires a FROM clause")

    retained: list[str] = []
    predictions: list[MLPredictExpression] = []
    feature_aliases: dict[str, str] = {}
    feature_projection: list[str] = []
    all_calls = _ml_predict_nodes(sql, tree=tree)
    if any(type(item).__name__ == "SelectAllContext" for item in items):
        raise UnsupportedMLSQLExpression("List output columns explicitly when using ML_PREDICT")

    for index, item in enumerate(items):
        calls = [
            call
            for call in all_calls
            if item.start.start <= call.start.start and call.stop.stop <= item.stop.stop
        ]
        if not calls:
            retained.append(_source(sql, item))
            continue
        if len(calls) != 1 or type(item).__name__ != "SelectSingleContext":
            raise UnsupportedMLSQLExpression(
                "ML_PREDICT must be a standalone top-level SELECT item"
            )
        call = calls[0]
        expression = item.expression()
        if expression.start.start != call.start.start or expression.stop.stop != call.stop.stop:
            raise UnsupportedMLSQLExpression(
                "Wrapped ML_PREDICT expressions are not supported; project the prediction "
                "with an alias and apply ROUND, CAST, CASE, or arithmetic in a follow-up query"
            )
        arguments = list(call.expression())
        if len(arguments) < 2:
            raise ValueError("ML_PREDICT requires an alias and at least one feature")
        alias_token = _source(sql, arguments[0]).strip()
        if not (
            arguments[0].start.tokenIndex == arguments[0].stop.tokenIndex
            and len(alias_token) >= 2
            and alias_token[0] == alias_token[-1] == "'"
            and "\\" not in alias_token
        ):
            raise ValueError("ML_PREDICT model alias must be a string literal")
        model_alias = alias_token[1:-1].replace("''", "'")
        hidden: list[str] = []
        feature_args: list[str] = []
        for argument in arguments[1:]:
            source = _source(sql, argument)
            # Source text keeps token boundaries distinct (for example, a + +b).
            canonical = source
            hidden_name = feature_aliases.get(canonical)
            if hidden_name is None:
                hidden_name = f"__ml_feature_{len(feature_aliases)}"
                feature_aliases[canonical] = hidden_name
                feature_projection.append(f"({source}) AS `{hidden_name}`")
            hidden.append(hidden_name)
            feature_args.append(source)
        prediction_name = _select_alias(item) or (
            "prediction" if not predictions else f"prediction_{len(predictions) + 1}"
        )
        predictions.append(
            MLPredictExpression(
                alias=model_alias,
                feature_args=tuple(feature_args),
                feature_columns=tuple(hidden),
                prediction_index=index,
                prediction_name=prediction_name,
            )
        )

    if not predictions:
        raise UnsupportedMLSQLExpression("ML_PREDICT was not found in the outer SELECT list")
    if len(predictions) != len(all_calls):
        raise UnsupportedMLSQLExpression(
            "ML_PREDICT is supported only in the outer SELECT list, not filters or subqueries"
        )
    if any("__ml_feature_" in item.getText().casefold() for item in items):
        raise UnsupportedMLSQLExpression(
            "The __ml_feature_ prefix is reserved for prediction inputs"
        )
    projection = retained + feature_projection
    projection_start = items[0].start.start
    feature_sql = (
        sql[:projection_start] + ", ".join(projection) + " " + sql[from_clause.start.start :]
    )
    first = predictions[0]
    return MLPredictRewrite(
        alias=first.alias,
        feature_sql=feature_sql,
        feature_columns=first.feature_columns,
        prediction_index=first.prediction_index,
        prediction_name=first.prediction_name,
        predictions=tuple(predictions),
    )


def _ml_predict_nodes(sql: str, *, tree=None) -> list:
    if tree is None:
        stream, tree, errors = _parse_tree(sql)
        if errors and any(
            token.text and token.text.strip("`").casefold() == "ml_predict" and token.channel == 0
            for token in stream.tokens
        ):
            raise UnsupportedMLSQLExpression("Cannot safely parse the ML_PREDICT statement")
    return [
        node
        for node in _walk_nodes(tree)
        if type(node).__name__ == "SimpleFunctionCallContext"
        and node.qualifiedName().getText().strip("`").casefold() == "ml_predict"
    ]


def _source(sql: str, node) -> str:
    return sql[node.start.start : node.stop.stop + 1]


def _select_alias(item) -> str | None:
    identifiers = item.identifier()
    if identifiers:
        node = identifiers[-1] if isinstance(identifiers, list) else identifiers
        return node.getText().strip("`")
    strings = item.string()
    if strings:
        node = strings[-1] if isinstance(strings, list) else strings
        return node.getText().strip("'").replace("''", "'")
    return None


def _split_args(args_str: str) -> list[str]:
    """Split function arguments by comma, respecting parentheses."""
    args = []
    current = []
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(args_str):
        ch = args_str[index]
        if quote:
            current.append(ch)
            if ch == quote:
                if index + 1 < len(args_str) and args_str[index + 1] == quote:
                    current.append(args_str[index + 1])
                    index += 2
                    continue
                quote = None
        elif ch in {"'", '"', "`"}:
            quote = ch
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        index += 1
    if current:
        args.append("".join(current).strip())
    return args


def _matching_parenthesis(sql: str, opening: int, *, function_name: str = "ML_PREDICT") -> int:
    depth = 0
    quote: str | None = None
    index = opening
    while index < len(sql):
        char = sql[index]
        if quote:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError(f"{function_name} has an unclosed parenthesis")


def _sql_string_literal(token: str, argument: str) -> str:
    token = token.strip()
    if len(token) < 2 or token[0] != token[-1] or token[0] != "'":
        raise ValueError(f"ML_FORECAST {argument} must be a string literal")
    return token[1:-1].replace("''", "'")


def _positive_integer(token: str, argument: str) -> int:
    if not re.fullmatch(r"[0-9]+", token.strip()):
        raise ValueError(f"ML_FORECAST {argument} must be a positive integer")
    value = int(token)
    if value < 1:
        raise ValueError(f"ML_FORECAST {argument} must be a positive integer")
    return value
