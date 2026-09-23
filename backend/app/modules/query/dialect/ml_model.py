"""Parser for Nova CREATE ML_MODEL worksheet DDL."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.modules.query.dialect.parser import _lex, _visible_tokens

_MODEL_NAME_PATTERN = re.compile(r"(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_$]*)\Z")
_CLAUSE_NAMES = frozenset(
    {
        "TYPE", "TARGET", "ALGORITHM", "TEST_SIZE", "FEATURES", "HYPERPARAMETERS",
        "TIMESTAMP", "SERIES", "HORIZON", "FREQUENCY", "MODE", "INPUT", "CONFIG",
    }
)
#: The model types the surface documents. `FORECAST` and `ANOMALY_DETECTION` are
#: Nova adaptations recorded in `docs/19-machine-learning.md:184,322` and
#: `AGENTS.md:245`; the previous pattern accepted only the two supervised types,
#: so a documented `TYPE = FORECAST` failed as a syntax error (NOVA-17 defect 7).
#: Keeping the set here — rather than a permissive identifier capture — is what
#: still rejects `TYPE = BANANA` with a useful message.
_TYPE_PATTERN = re.compile(
    r"\bTYPE\s*=\s*(?P<value>CLASSIFICATION|REGRESSION|FORECAST|ANOMALY_DETECTION|CLUSTERING)\b",
    re.IGNORECASE,
)
_TARGET_PATTERN = re.compile(
    r"\bTARGET\s*=\s*(?P<value>`[^`]+`|'[^']+'|[A-Za-z_][A-Za-z0-9_$]*)",
    re.IGNORECASE,
)
_ALGORITHM_PATTERN = re.compile(
    r"\bALGORITHM\s*=\s*(?P<value>[A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_TIMESTAMP_PATTERN = re.compile(
    r"\bTIMESTAMP\s*=\s*(?P<value>`[^`]+`|'[^']+'|[A-Za-z_][A-Za-z0-9_$]*)",
    re.IGNORECASE,
)
_SERIES_PATTERN = re.compile(
    r"\bSERIES\s*=\s*(?P<value>`[^`]+`|'[^']+'|[A-Za-z_][A-Za-z0-9_$]*)",
    re.IGNORECASE,
)
_HORIZON_PATTERN = re.compile(r"\bHORIZON\s*=\s*(?P<value>\d+)", re.IGNORECASE)
_FREQUENCY_PATTERN = re.compile(r"\bFREQUENCY\s*=\s*'(?P<value>[^']+)'", re.IGNORECASE)
_MODE_PATTERN = re.compile(r"\bMODE\s*=\s*(?P<value>INTERACTIVE|BALANCED|BEST)\b", re.IGNORECASE)
_TEST_SIZE_PATTERN = re.compile(
    r"\bTEST_SIZE\s*=\s*(?P<value>0(?:\.\d+)?|1(?:\.0+)?)\b",
    re.IGNORECASE,
)
_FEATURES_PATTERN = re.compile(
    r"\bFEATURES\s*=\s*\((?P<value>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
_HYPERPARAMETERS_PATTERN = re.compile(
    r"\bHYPERPARAMETERS\s*=\s*(?:JSON\s*)?'(?P<value>(?:''|[^'])*)'",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class CreateMLModelStatement:
    """Parsed compact CREATE ML_MODEL statement."""

    model_name: str
    model_type: str
    target_column: str | None
    training_sql: str
    algorithm: str = "auto"
    test_size: float = 0.2
    feature_columns: list[str] | None = None
    hyperparameters: dict[str, Any] | None = None
    timestamp_column: str | None = None
    series_column: str | None = None
    horizon: int | None = None
    frequency: str | None = None
    mode: str = "balanced"


def is_create_ml_model(sql: str) -> bool:
    """Return True when SQL starts with Nova's CREATE ML_MODEL DDL."""
    return bool(re.match(r"^\s*CREATE\s+ML_MODEL\b", sql, re.IGNORECASE))


def parse_create_ml_model(sql: str) -> CreateMLModelStatement:
    """Parse compact CREATE ML_MODEL DDL.

    Supported v1 syntax:
        CREATE ML_MODEL name
        TYPE = CLASSIFICATION|REGRESSION|FORECAST|ANOMALY_DETECTION
        TARGET = target_column
        [ALGORITHM = random_forest]
        [TEST_SIZE = 0.2]
        [FEATURES = (feature1, feature2)]
        [HYPERPARAMETERS = JSON '{"n_estimators": 100}']
        AS SELECT ...
    """
    tokens = _visible_tokens(_lex(sql))
    if len(tokens) < 4 or [token.text.upper() for token in tokens[:2]] != ["CREATE", "ML_MODEL"]:
        raise ValueError(
            "Invalid CREATE ML_MODEL syntax. Expected: CREATE ML_MODEL name "
            "TYPE = CLASSIFICATION|REGRESSION|FORECAST|ANOMALY_DETECTION|CLUSTERING "
            "AS SELECT ..."
        )
    model_name = tokens[2].text
    if not _MODEL_NAME_PATTERN.fullmatch(model_name):
        raise ValueError("Invalid CREATE ML_MODEL model name")

    clauses, training_sql = _split_model_clauses(sql, tokens)
    type_match = _TYPE_PATTERN.fullmatch(clauses.get("TYPE", ""))
    target_match = _TARGET_PATTERN.fullmatch(clauses.get("TARGET", ""))
    if not type_match:
        raise ValueError(
            "CREATE ML_MODEL requires TYPE = CLASSIFICATION, REGRESSION, "
            "FORECAST, ANOMALY_DETECTION, or CLUSTERING"
        )
    model_type = type_match.group("value").lower()
    if model_type in {"classification", "regression", "forecast"} and not target_match:
        raise ValueError(f"CREATE ML_MODEL TYPE={model_type.upper()} requires TARGET")

    patterns = {
        "TARGET": _TARGET_PATTERN,
        "ALGORITHM": _ALGORITHM_PATTERN,
        "TEST_SIZE": _TEST_SIZE_PATTERN,
        "FEATURES": _FEATURES_PATTERN,
        "HYPERPARAMETERS": _HYPERPARAMETERS_PATTERN,
        "TIMESTAMP": _TIMESTAMP_PATTERN,
        "SERIES": _SERIES_PATTERN,
        "HORIZON": _HORIZON_PATTERN,
        "FREQUENCY": _FREQUENCY_PATTERN,
        "MODE": _MODE_PATTERN,
    }
    matches = {}
    for name, pattern in patterns.items():
        if name in clauses:
            match = pattern.fullmatch(clauses[name])
            if match is None:
                raise ValueError(f"Invalid CREATE ML_MODEL {name} clause")
            matches[name] = match

    algorithm_match = matches.get("ALGORITHM")
    test_size_match = matches.get("TEST_SIZE")
    features_match = matches.get("FEATURES")
    hyperparameters_match = matches.get("HYPERPARAMETERS")
    timestamp_match = matches.get("TIMESTAMP")
    series_match = matches.get("SERIES")
    horizon_match = matches.get("HORIZON")
    frequency_match = matches.get("FREQUENCY")
    mode_match = matches.get("MODE")

    test_size = float(test_size_match.group("value")) if test_size_match else 0.2
    if not 0 <= test_size < 1:
        raise ValueError("CREATE ML_MODEL TEST_SIZE must be >= 0 and < 1")
    horizon = int(horizon_match.group("value")) if horizon_match else None
    if horizon is not None and horizon < 1:
        raise ValueError("CREATE ML_MODEL HORIZON must be positive")

    hyperparameters = None
    if hyperparameters_match:
        raw_json = hyperparameters_match.group("value").replace("''", "'")
        try:
            parsed_json = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid HYPERPARAMETERS JSON: {exc.msg}") from exc
        if not isinstance(parsed_json, dict):
            raise ValueError("CREATE ML_MODEL HYPERPARAMETERS must be a JSON object")
        hyperparameters = parsed_json

    return CreateMLModelStatement(
        model_name=_unquote_identifier(model_name),
        model_type=model_type,
        target_column=(_unquote_identifier(target_match.group("value")) if target_match else None),
        training_sql=training_sql,
        algorithm=(algorithm_match.group("value").lower() if algorithm_match else "auto"),
        test_size=test_size,
        feature_columns=_parse_features(features_match.group("value")) if features_match else None,
        hyperparameters=hyperparameters,
        timestamp_column=(
            _unquote_identifier(timestamp_match.group("value")) if timestamp_match else None
        ),
        series_column=(_unquote_identifier(series_match.group("value")) if series_match else None),
        horizon=horizon,
        frequency=frequency_match.group("value") if frequency_match else None,
        mode=mode_match.group("value").lower() if mode_match else "balanced",
    )


def _split_model_clauses(sql: str, tokens: list) -> tuple[dict[str, str], str]:
    """Read top-level assignments without treating nested SQL or strings as clauses."""
    end = len(tokens)
    if tokens[-1].text == ";":
        end -= 1
    as_index = end
    depth = 0
    for index in range(3, end):
        word = tokens[index].text.upper()
        if word == "(":
            depth += 1
        elif word == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("Invalid CREATE ML_MODEL syntax: unmatched parenthesis")
        elif (
            depth == 0
            and word == "AS"
            and index + 1 < end
            and tokens[index + 1].text.upper() == "SELECT"
        ):
            as_index = index
            break
    if depth != 0:
        raise ValueError("Invalid CREATE ML_MODEL syntax: unmatched parenthesis")

    clauses: dict[str, str] = {}
    index = 3
    while index < as_index:
        name = tokens[index].text.upper()
        if name not in _CLAUSE_NAMES:
            raise ValueError(f"Unsupported CREATE ML_MODEL clause: {name}")
        if index + 1 >= as_index or tokens[index + 1].text != "=":
            raise ValueError(f"Invalid CREATE ML_MODEL {name} clause")
        if name in clauses:
            raise ValueError(f"Duplicate CREATE ML_MODEL {name} clause")
        start = index
        index += 2
        depth = 0
        while index < as_index:
            word = tokens[index].text.upper()
            if word == "(":
                depth += 1
            elif word == ")":
                depth -= 1
                if depth < 0:
                    raise ValueError("Invalid CREATE ML_MODEL syntax: unmatched parenthesis")
            elif depth == 0 and index + 1 < as_index and tokens[index + 1].text == "=":
                break
            index += 1
        if depth != 0:
            raise ValueError("Invalid CREATE ML_MODEL syntax: unmatched parenthesis")
        if index == start + 2:
            raise ValueError(f"Invalid CREATE ML_MODEL {name} clause")
        clauses[name] = sql[tokens[start].start : tokens[index - 1].stop + 1].strip()

    if "CONFIG" in clauses:
        raise ValueError("CREATE ML_MODEL CONFIG is not supported; use HYPERPARAMETERS")
    if as_index < end:
        training_sql = sql[tokens[as_index + 1].start : tokens[end - 1].stop + 1].strip()
        if "INPUT" in clauses:
            raise ValueError("CREATE ML_MODEL accepts either INPUT or AS SELECT, not both")
    elif "INPUT" in clauses:
        value = clauses["INPUT"].split("=", 1)[1].strip()
        if not (value.startswith("(") and value.endswith(")")):
            raise ValueError("CREATE ML_MODEL INPUT must be a parenthesized SELECT")
        training_sql = value[1:-1].strip()
    else:
        raise ValueError(
            "Invalid CREATE ML_MODEL syntax: expected AS SELECT or INPUT = (SELECT ...)"
        )
    if not re.match(r"SELECT\b", training_sql, re.IGNORECASE):
        raise ValueError("CREATE ML_MODEL training query must begin with SELECT")
    return clauses, training_sql


def _parse_features(raw: str) -> list[str]:
    features = [_unquote_identifier(part.strip()) for part in raw.split(",") if part.strip()]
    if not features:
        raise ValueError("CREATE ML_MODEL FEATURES must include at least one column")
    return features


def _unquote_identifier(value: str) -> str:
    value = value.strip()
    if (value.startswith("`") and value.endswith("`")) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value
