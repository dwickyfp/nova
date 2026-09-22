"""ML intercept — detect and execute ml_predict() calls in SQL.

When a user writes:
  SELECT ml_predict('status_predictor', total_amount) FROM NOVA_EXAMPLE.orders

The backend intercepts this, executes the inner query to fetch features,
runs the ML model prediction on each row, and returns the combined result.

Supported patterns:
  1. ml_predict('alias', column1, column2, ...) — features from columns
  2. ml_predict('alias', json_string) — features as JSON
"""

import re
from dataclasses import dataclass


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
    """Find one balanced ``ML_PREDICT`` call outside strings/comments."""
    name = "ML_PREDICT"
    index = 0
    quote: str | None = None
    while index < len(sql):
        char = sql[index]
        if quote:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = len(sql) if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            index = len(sql) if end < 0 else end + 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if sql[index : index + len(name)].upper() == name:
            before = sql[index - 1] if index else " "
            after_name = index + len(name)
            if before.isalnum() or before == "_":
                index += 1
                continue
            cursor = after_name
            while cursor < len(sql) and sql[cursor].isspace():
                cursor += 1
            if cursor >= len(sql) or sql[cursor] != "(":
                index += 1
                continue
            close = _matching_parenthesis(sql, cursor)
            arguments = _split_args(sql[cursor + 1 : close])
            if len(arguments) < 2:
                raise ValueError("ML_PREDICT requires an alias and at least one feature")
            alias_token = arguments[0].strip()
            if not (len(alias_token) >= 2 and alias_token[0] == alias_token[-1] == "'"):
                raise ValueError("ML_PREDICT model alias must be a string literal")
            alias = alias_token[1:-1].replace("''", "'")
            return MLPredictCall(index, close + 1, alias, tuple(arguments[1:]))
        index += 1
    return None


def rewrite_ml_predict_sql(sql: str, match: MLPredictCall) -> tuple[str, str, list[str]]:
    """Rewrite SQL to extract the inner query without ml_predict wrapper.

    Args:
        sql: Original SQL with ml_predict() call
        match: Regex match from detect_ml_predict

    Returns:
        Tuple of (alias, inner_sql, feature_args)
        - alias: model alias name
        - inner_sql: SQL to execute to get feature data
        - feature_args: list of feature column expressions
    """
    alias = match.group(1)
    features = list(match.feature_args)

    # Execute a dedicated feature query, then append vectorized predictions in
    # Nova. Keeping the original FROM/WHERE/GROUP/ORDER tail delegates all data
    # processing to StarRocks while ensuring no scalar UDF or per-row HTTP call
    # remains in the active path.
    from_index = _top_level_from(sql, match.end())
    if from_index < 0:
        raise ValueError("ML_PREDICT requires a FROM clause")
    inner_sql = "SELECT " + ", ".join(features) + " " + sql[from_index:]

    return alias, inner_sql, features


def rewrite_ml_predict_projection(sql: str, match: MLPredictCall) -> MLPredictRewrite:
    """Build a feature query while retaining the user's projection contract."""
    select_start = _select_projection_start(sql)
    from_index = _top_level_from(sql, select_start)
    if from_index < 0:
        raise ValueError("ML_PREDICT requires a FROM clause")
    projection_text = sql[select_start:from_index]
    items = _split_args(projection_text)
    relative_start = match.start() - select_start
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for item in items:
        found = projection_text.find(item, cursor)
        offsets.append((found, found + len(item)))
        cursor = found + len(item)
    prediction_index = next(
        (index for index, (start, end) in enumerate(offsets) if start <= relative_start < end),
        -1,
    )
    if prediction_index < 0:
        raise ValueError("ML_PREDICT must appear in the top-level SELECT projection")
    prediction_item = items[prediction_index]
    call_end_in_item = match.end() - select_start - offsets[prediction_index][0]
    suffix = prediction_item[call_end_in_item:].strip()
    alias_match = re.fullmatch(
        r"(?:AS\s+)?(`[^`]+`|[A-Za-z_][A-Za-z0-9_$]*)", suffix, re.IGNORECASE
    )
    prediction_name = alias_match.group(1).strip("`") if alias_match else "prediction"
    retained = [item for index, item in enumerate(items) if index != prediction_index]
    hidden = tuple(f"__ml_feature_{index}" for index in range(len(match.feature_args)))
    feature_projection = [
        f"({expression}) AS `{name}`"
        for expression, name in zip(match.feature_args, hidden, strict=True)
    ]
    inner_projection = retained + feature_projection
    return MLPredictRewrite(
        alias=match.alias,
        feature_sql="SELECT " + ", ".join(inner_projection) + " " + sql[from_index:],
        feature_columns=hidden,
        prediction_index=prediction_index,
        prediction_name=prediction_name,
    )


def _top_level_from(sql: str, start: int) -> int:
    depth = 0
    quote: str | None = None
    index = start
    while index < len(sql):
        char = sql[index]
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and sql[index : index + 4].upper() == "FROM":
            before = sql[index - 1] if index else " "
            after = sql[index + 4] if index + 4 < len(sql) else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                return index
        index += 1
    return -1


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


def _select_projection_start(sql: str) -> int:
    match = re.match(r"\s*SELECT\b", sql, re.IGNORECASE)
    if not match:
        raise ValueError("ML_PREDICT interception currently requires a SELECT statement")
    return match.end()
