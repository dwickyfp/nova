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

    # Replace ml_predict(...) with a placeholder column
    # We'll replace the entire ml_predict(...) call with NULL as ml_prediction
    # and add the feature columns to the SELECT
    inner_sql = sql[: match.start()] + "NULL AS __ml_prediction__" + sql[match.end() :]

    return alias, inner_sql, features


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
