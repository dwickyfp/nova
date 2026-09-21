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

# Pattern: ml_predict('alias', ...rest...) — captures alias and feature args
ML_PREDICT_PATTERN = re.compile(
    r"ml_predict\s*\(\s*'([^']+)'\s*,\s*(.+?)\s*\)",
    re.IGNORECASE,
)


def detect_ml_predict(sql: str) -> re.Match | None:
    """Check if SQL contains an ml_predict() call.

    Returns the regex match if found, None otherwise.
    """
    return ML_PREDICT_PATTERN.search(sql)


def rewrite_ml_predict_sql(sql: str, match: re.Match) -> tuple[str, str, list[str]]:
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
    feature_args_str = match.group(2)

    # Parse feature args (split by comma, respect parentheses)
    features = _split_args(feature_args_str)

    # Execute a dedicated feature query, then append vectorized predictions in
    # Nova. Keeping the original FROM/WHERE/GROUP/ORDER tail delegates all data
    # processing to StarRocks while ensuring no scalar UDF or per-row HTTP call
    # remains in the active path.
    from_index = _top_level_from(sql, match.end())
    if from_index < 0:
        raise ValueError("ML_PREDICT requires a FROM clause")
    inner_sql = "SELECT " + ", ".join(features) + " " + sql[from_index:]

    return alias, inner_sql, features


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
            if not (before.isalnum() or before == "_") and not (
                after.isalnum() or after == "_"
            ):
                return index
        index += 1
    return -1


def _split_args(args_str: str) -> list[str]:
    """Split function arguments by comma, respecting parentheses."""
    args = []
    current = []
    depth = 0
    in_string = False
    for ch in args_str:
        if ch == "'" and not in_string:
            in_string = True
            current.append(ch)
        elif ch == "'" and in_string:
            in_string = False
            current.append(ch)
        elif ch == "(" and not in_string:
            depth += 1
            current.append(ch)
        elif ch == ")" and not in_string:
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0 and not in_string:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        args.append("".join(current).strip())
    return args
