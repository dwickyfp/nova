"""Semantic model parsing — Ossie (Open Semantic Interchange) documents.

Ossie is the vendor-neutral semantic standard (formerly Open Semantic
Interchange, now ``apache/ossie``, Apache-2.0). A semantic model is a YAML or
JSON document describing datasets, fields, metrics, and relationships. Nova
stores the *parsed* metadata, never raw text, and grounds ``semantic_query`` on
it.

Version discipline is deliberate. The spec at the time of writing has a stable
release (``0.1.1``, 2025-12-11) and a breaking draft (``0.2.0.dev0``). Nova
accepts only the versions in :data:`SUPPORTED_VERSIONS` and fails closed on
anything else: guessing at an unknown shape would produce wrong SQL, and a
semantic model that cannot be trusted is worse than one that is refused.

The parser is pure: it performs no I/O and touches no database. Credential
screening happens here too, because a semantic model is user-supplied text and
must never become a place a secret is stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml  # type: ignore[import-untyped]

from app.modules.assistant.skills import contains_credential_shape

#: Ossie versions Nova can parse. ``0.1.1`` is the initial stable release.
#: ``0.2.0.dev0`` is an in-development draft with a breaking document shape and
#: is intentionally excluded.
SUPPORTED_VERSIONS = ("0.1.1",)

#: Version strings we recognise but refuse, with the reason, so the error can
#: tell the author what to do instead of "unknown version".
_KNOWN_UNSUPPORTED = {
    "0.2.0.dev0": (
        "Ossie 0.2.0.dev0 is an in-development draft with a breaking document "
        "shape (the top-level 'semantic_model' array was removed). Migrate the "
        "model to 0.1.1, or wait until 0.2.0 is released and Nova adds support."
    ),
}

#: Expression dialects Nova can compile to StarRocks. ANSI_SQL is the safe
#: common denominator; StarRocks-specific dialect text is not itself an Ossie
#: dialect, so a model should express metrics in ANSI_SQL.
_PREFERRED_DIALECTS = ("ANSI_SQL",)


class OssieParseError(ValueError):
    """Raised when a document cannot be used as an Ossie semantic model."""


@dataclass
class ParseResult:
    """Outcome of a parse. ``valid`` is the only field a caller must branch on."""

    valid: bool
    version: str | None = None
    model: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dataset_count: int = 0
    metric_count: int = 0
    relationship_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        """The stored ``definition``: the parsed model plus its version."""
        return {"version": self.version, **self.model}


def parse_ossie(text: str, *, raise_on_error: bool = True) -> ParseResult:
    """Parse and validate an Ossie document.

    ``raise_on_error=True`` (the default) raises :class:`OssieParseError` on an
    invalid document — the create path wants a hard failure. ``False`` returns a
    :class:`ParseResult` with ``valid=False`` and populated ``errors`` — the
    validate endpoint wants to show the author what is wrong.
    """
    result = ParseResult(valid=False)

    if not isinstance(text, str) or not text.strip():
        result.errors.append("The definition is empty.")
        return _finish(result, raise_on_error)

    if contains_credential_shape(text):
        # A semantic model is metadata; a credential in it is always a mistake
        # and is exactly the leak this screen exists to stop.
        result.errors.append(
            "The definition contains a credential-shaped value. "
            "Semantic models must not carry credentials."
        )
        return _finish(result, raise_on_error)

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        result.errors.append(f"The definition is not valid YAML: {exc}")
        return _finish(result, raise_on_error)

    if not isinstance(document, dict):
        result.errors.append("The definition must be a mapping (an Ossie document).")
        return _finish(result, raise_on_error)

    version = _as_str(document.get("version"))
    result.version = version

    if version is None:
        result.errors.append("Missing required 'version' field (for example 'version: 0.1.1').")
        return _finish(result, raise_on_error)

    if version not in SUPPORTED_VERSIONS:
        reason = _KNOWN_UNSUPPORTED.get(version)
        if reason:
            result.errors.append(f"Unsupported Ossie version {version!r}. {reason}")
        else:
            supported = ", ".join(SUPPORTED_VERSIONS)
            result.errors.append(f"Unsupported Ossie version {version!r}. Supported: {supported}.")
        return _finish(result, raise_on_error)

    _validate_document(document, result)

    model = _normalise(document)
    result.model = model
    result.dataset_count = len(model.get("datasets", []))
    result.metric_count = len(model.get("metrics", []))
    result.relationship_count = len(model.get("relationships", []))

    result.valid = not result.errors
    return _finish(result, raise_on_error)


# ── Validation ─────────────────────────────────────────────────


def _validate_document(document: dict, result: ParseResult) -> None:
    name = document.get("name")
    if not isinstance(name, str) or not name.strip():
        result.errors.append("Missing required 'name' field.")

    datasets = document.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        result.errors.append("'datasets' is required and must be a non-empty list.")
        return

    dataset_names: set[str] = set()
    for index, dataset in enumerate(datasets):
        _validate_dataset(dataset, index, dataset_names, result)

    _validate_relationships(document.get("relationships"), dataset_names, result)
    _validate_metrics(document.get("metrics"), result)


def _validate_dataset(dataset: Any, index: int, names: set[str], result: ParseResult) -> None:
    if not isinstance(dataset, dict):
        result.errors.append(f"datasets[{index}] must be a mapping.")
        return
    name = dataset.get("name")
    if not isinstance(name, str) or not name.strip():
        result.errors.append(f"datasets[{index}] is missing a 'name'.")
        return
    if name in names:
        result.errors.append(f"datasets[{index}] duplicates dataset name {name!r}.")
    names.add(name)

    source = dataset.get("source")
    if not isinstance(source, str) or not source.strip():
        result.errors.append(f"dataset {name!r} is missing a 'source'.")

    fields = dataset.get("fields")
    if fields is not None and not isinstance(fields, list):
        result.errors.append(f"dataset {name!r}: 'fields' must be a list.")
        return
    for f_index, fld in enumerate(fields or []):
        if not isinstance(fld, dict) or not isinstance(fld.get("name"), str):
            result.errors.append(f"dataset {name!r} fields[{f_index}] is missing a 'name'.")
            continue
        expression = fld.get("expression")
        if not isinstance(expression, dict):
            result.errors.append(
                f"dataset {name!r} field {fld['name']!r} is missing an 'expression'."
            )
            continue
        if not _expression_has_dialect(expression):
            result.errors.append(
                f"dataset {name!r} field {fld['name']!r} expression has no usable "
                f"dialect ({', '.join(_PREFERRED_DIALECTS)})."
            )


def _validate_relationships(
    relationships: Any, dataset_names: set[str], result: ParseResult
) -> None:
    if relationships is None:
        return
    if not isinstance(relationships, list):
        result.errors.append("'relationships' must be a list.")
        return
    for index, rel in enumerate(relationships):
        if not isinstance(rel, dict):
            result.errors.append(f"relationships[{index}] must be a mapping.")
            continue
        for key in ("name", "from", "to"):
            if not isinstance(rel.get(key), str) or not rel.get(key, "").strip():
                result.errors.append(f"relationships[{index}] is missing {key!r}.")
        from_cols = rel.get("from_columns")
        to_cols = rel.get("to_columns")
        if not isinstance(from_cols, list) or not isinstance(to_cols, list):
            result.errors.append(
                f"relationship {rel.get('name', index)!r} needs 'from_columns' and "
                "'to_columns' lists."
            )
            continue
        if len(from_cols) != len(to_cols):
            result.errors.append(
                f"relationship {rel.get('name', index)!r}: 'from_columns' and "
                "'to_columns' must have the same length."
            )
        for side in ("from", "to"):
            target = rel.get(side)
            if isinstance(target, str) and dataset_names and target not in dataset_names:
                result.warnings.append(
                    f"relationship {rel.get('name', index)!r} references unknown "
                    f"dataset {target!r} in '{side}'."
                )


def _validate_metrics(metrics: Any, result: ParseResult) -> None:
    if metrics is None:
        return
    if not isinstance(metrics, list):
        result.errors.append("'metrics' must be a list.")
        return
    for index, metric in enumerate(metrics):
        if not isinstance(metric, dict) or not isinstance(metric.get("name"), str):
            result.errors.append(f"metrics[{index}] is missing a 'name'.")
            continue
        expression = metric.get("expression")
        if not isinstance(expression, dict) or not _expression_has_dialect(expression):
            result.errors.append(
                f"metric {metric['name']!r} is missing a usable 'expression' "
                f"({', '.join(_PREFERRED_DIALECTS)})."
            )


def _expression_has_dialect(expression: dict) -> bool:
    dialects = expression.get("dialects")
    if not isinstance(dialects, list):
        return False
    for entry in dialects:
        if not isinstance(entry, dict):
            continue
        dialect = _as_str(entry.get("dialect"))
        text = entry.get("expression")
        if dialect in _PREFERRED_DIALECTS and isinstance(text, str) and text.strip():
            return True
    return False


# ── Normalisation ──────────────────────────────────────────────


def _normalise(document: dict) -> dict[str, Any]:
    """Return only the fields Nova stores, with stable shapes.

    The stored definition is metadata only. Field/metric expressions are kept as
    the frozen dialect strings so grounding is deterministic; no other
    document keys are carried through.
    """
    datasets: list[dict[str, Any]] = []
    for dataset in document.get("datasets") or []:
        datasets.append(
            {
                "name": dataset.get("name"),
                "source": dataset.get("source"),
                "description": dataset.get("description", ""),
                "primary_key": dataset.get("primary_key") or [],
                "unique_keys": dataset.get("unique_keys") or [],
                "grain": dataset.get("grain") or {
                    "keys": dataset.get("primary_key") or []
                },
                "synonyms": dataset.get("synonyms") or [],
                "ai_context": dataset.get("ai_context"),
                "fields": [_normalise_field(f) for f in dataset.get("fields") or []],
            }
        )

    relationships: list[dict[str, Any]] = []
    for rel in document.get("relationships") or []:
        relationships.append(
            {
                "name": rel.get("name"),
                "from": rel.get("from"),
                "to": rel.get("to"),
                "from_columns": rel.get("from_columns") or [],
                "to_columns": rel.get("to_columns") or [],
                "cardinality": rel.get("cardinality") or "unknown",
                "preferred": bool(rel.get("preferred", False)),
                "ai_context": rel.get("ai_context"),
            }
        )

    metrics: list[dict[str, Any]] = []
    for metric in document.get("metrics") or []:
        metrics.append(
            {
                "name": metric.get("name"),
                "expression": _expression_text(metric.get("expression")),
                "description": metric.get("description", ""),
                "datatype": metric.get("datatype"),
                "base_dataset": metric.get("base_dataset"),
                "grain": metric.get("grain") or {},
                "additivity": metric.get("additivity") or "additive",
                "default_time_dimension": metric.get("default_time_dimension"),
                "allowed_dimensions": metric.get("allowed_dimensions") or [],
                "non_additive_dimensions": metric.get("non_additive_dimensions") or [],
                "synonyms": metric.get("synonyms") or [],
                "format": metric.get("format"),
                "currency": metric.get("currency"),
                "unit": metric.get("unit"),
                "dependencies": metric.get("dependencies") or [],
                "filters": metric.get("filters") or [],
                "visibility": metric.get("visibility") or "public",
                "preferred_relationship_path": metric.get("preferred_relationship_path") or [],
                "ai_context": metric.get("ai_context"),
            }
        )

    return {
        "name": document.get("name"),
        "description": document.get("description", ""),
        "ai_context": document.get("ai_context"),
        "entities": document.get("entities") or {},
        "hierarchies": document.get("hierarchies") or {},
        "verified_queries": document.get("verified_queries") or [],
        "datasets": datasets,
        "relationships": relationships,
        "metrics": metrics,
        "named_filters": [
            {
                "name": item.get("name"),
                "expression": item.get("expression"),
                "dataset": item.get("dataset"),
                "description": item.get("description", ""),
                "synonyms": item.get("synonyms") or [],
                "ai_context": item.get("ai_context"),
            }
            for item in document.get("named_filters") or []
            if isinstance(item, dict)
        ],
        "question_routing_instructions": document.get(
            "question_routing_instructions", ""
        ),
        "query_generation_instructions": document.get(
            "query_generation_instructions", ""
        ),
    }


def _normalise_field(field: dict) -> dict[str, Any]:
    return {
        "name": field.get("name"),
        "expression": _expression_text(field.get("expression")),
        "description": field.get("description", ""),
        "datatype": field.get("datatype"),
        "dimension": field.get("dimension"),
        "kind": field.get("kind"),
        "synonyms": field.get("synonyms") or [],
        "sample_values": field.get("sample_values") or [],
        "search_strategy": field.get("search_strategy"),
        "ai_context": field.get("ai_context"),
    }


def _expression_text(expression: Any) -> str:
    """The preferred-dialect expression text, or the first available one."""
    if not isinstance(expression, dict):
        return ""
    dialects = expression.get("dialects")
    if not isinstance(dialects, list):
        return ""
    for entry in dialects:
        if not isinstance(entry, dict):
            continue
        if entry.get("dialect") in _PREFERRED_DIALECTS:
            return _as_str(entry.get("expression")) or ""
    for entry in dialects:
        if isinstance(entry, dict):
            text = _as_str(entry.get("expression"))
            if text:
                return text
    return ""


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _finish(result: ParseResult, raise_on_error: bool) -> ParseResult:
    if raise_on_error and not result.valid:
        raise OssieParseError("; ".join(result.errors) or "Invalid Ossie document.")
    return result
