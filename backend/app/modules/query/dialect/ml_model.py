"""Parser for Nova CREATE ML_MODEL worksheet DDL."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_CREATE_ML_MODEL_PATTERN = re.compile(
    r"^\s*CREATE\s+ML_MODEL\s+(?P<model_name>`[^`]+`|[A-Za-z_][A-Za-z0-9_$]*)"
    r"\s+(?P<body>.*?)\s+AS\s+(?P<training_sql>SELECT\b.+)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TYPE_PATTERN = re.compile(
    r"\bTYPE\s*=\s*(?P<value>CLASSIFICATION|REGRESSION)\b",
    re.IGNORECASE,
)
_TARGET_PATTERN = re.compile(
    r"\bTARGET\s*=\s*(?P<value>`[^`]+`|[A-Za-z_][A-Za-z0-9_$]*)",
    re.IGNORECASE,
)
_ALGORITHM_PATTERN = re.compile(
    r"\bALGORITHM\s*=\s*(?P<value>auto|linear|logistic|decision_tree|random_forest|gradient_boost|knn|svm)\b",
    re.IGNORECASE,
)
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
    target_column: str
    training_sql: str
    algorithm: str = "auto"
    test_size: float = 0.2
    feature_columns: list[str] | None = None
    hyperparameters: dict[str, Any] | None = None


def is_create_ml_model(sql: str) -> bool:
    """Return True when SQL starts with Nova's CREATE ML_MODEL DDL."""
    return bool(re.match(r"^\s*CREATE\s+ML_MODEL\b", sql, re.IGNORECASE))


def parse_create_ml_model(sql: str) -> CreateMLModelStatement:
    """Parse compact CREATE ML_MODEL DDL.

    Supported v1 syntax:
        CREATE ML_MODEL name
        TYPE = CLASSIFICATION|REGRESSION
        TARGET = target_column
        [ALGORITHM = random_forest]
        [TEST_SIZE = 0.2]
        [FEATURES = (feature1, feature2)]
        [HYPERPARAMETERS = JSON '{"n_estimators": 100}']
        AS SELECT ...
    """
    match = _CREATE_ML_MODEL_PATTERN.match(sql)
    if not match:
        raise ValueError(
            "Invalid CREATE ML_MODEL syntax. Expected: CREATE ML_MODEL name "
            "TYPE = CLASSIFICATION|REGRESSION TARGET = target AS SELECT ..."
        )

    body = match.group("body")
    type_match = _TYPE_PATTERN.search(body)
    target_match = _TARGET_PATTERN.search(body)
    if not type_match:
        raise ValueError("CREATE ML_MODEL requires TYPE = CLASSIFICATION or TYPE = REGRESSION")
    if not target_match:
        raise ValueError("CREATE ML_MODEL requires TARGET = target_column")

    algorithm_match = _ALGORITHM_PATTERN.search(body)
    test_size_match = _TEST_SIZE_PATTERN.search(body)
    features_match = _FEATURES_PATTERN.search(body)
    hyperparameters_match = _HYPERPARAMETERS_PATTERN.search(body)

    test_size = float(test_size_match.group("value")) if test_size_match else 0.2
    if not 0 <= test_size < 1:
        raise ValueError("CREATE ML_MODEL TEST_SIZE must be >= 0 and < 1")

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
        model_name=_unquote_identifier(match.group("model_name")),
        model_type=type_match.group("value").lower(),
        target_column=_unquote_identifier(target_match.group("value")),
        training_sql=match.group("training_sql").strip(),
        algorithm=(algorithm_match.group("value").lower() if algorithm_match else "auto"),
        test_size=test_size,
        feature_columns=_parse_features(features_match.group("value")) if features_match else None,
        hyperparameters=hyperparameters,
    )


def _parse_features(raw: str) -> list[str]:
    features = [_unquote_identifier(part.strip()) for part in raw.split(",") if part.strip()]
    if not features:
        raise ValueError("CREATE ML_MODEL FEATURES must include at least one column")
    return features


def _unquote_identifier(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`"):
        return value[1:-1]
    return value
