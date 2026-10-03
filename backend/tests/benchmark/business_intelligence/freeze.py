"""Freeze implementation, observation data, provider metadata and isolated corpora."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.benchmark.business_intelligence.gold.cases import export_holdout
from tests.benchmark.business_intelligence.learning_corpus import export_learning


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def implementation_manifest(root: Path) -> dict:
    files = {}
    for directory in (
        "backend/app",
        "frontend/src",
        "backend/tests/benchmark/business_intelligence",
    ):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".tsx", ".ts", ".json", ".yaml"}:
                files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in (
        "backend/uv.lock",
        "backend/pyproject.toml",
        "frontend/pnpm-lock.yaml",
        "frontend/package.json",
    ):
        files[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    return {"files": files, "hash": digest(files)}


def freeze(root: Path, dataset: dict, provider: dict, destination: Path) -> dict:
    allowed = {
        "mode",
        "provider_id",
        "model",
        "capabilities",
        "temperature",
        "max_output_tokens",
        "transport_fingerprint",
    }
    if (
        set(provider) - allowed
        or not provider.get("model")
        or provider.get("mode") not in {"scripted", "live"}
    ):
        raise ValueError("Supply explicit public provider configuration only")
    if destination.exists():
        raise ValueError("A frozen manifest cannot be overwritten")
    corpora = {
        "learning": export_learning(),
        "holdout_a": export_holdout("A"),
        "holdout_b": export_holdout("B"),
    }
    all_ids = [row["id"] for rows in corpora.values() for row in rows]
    if len(set(all_ids)) != 180 or [len(rows) for rows in corpora.values()] != [80, 60, 40]:
        raise ValueError("Frozen corpus cardinality or identity contract failed")
    learning_prompts = {row["content"] for row in corpora["learning"]}
    if any(
        row["question"] in learning_prompts
        for split in ("holdout_a", "holdout_b")
        for row in corpora[split]
    ):
        raise ValueError("A holdout question overlaps a teaching prompt")
    manifest = {
        "version": 1,
        "implementation": implementation_manifest(root),
        "dataset": dataset,
        "provider": provider,
        "corpora": {
            key: {"sha256": digest(value), "count": len(value)} for key, value in corpora.items()
        },
        "heldout_observations_immutable": True,
        "outcome_training_separate": True,
    }
    manifest["sha256"] = digest(manifest)
    destination.mkdir(parents=True)
    for key, value in corpora.items():
        (destination / f"{key}.json").write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        )
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify(root: Path, manifest: dict, dataset: dict, provider: dict) -> None:
    if digest({k: v for k, v in manifest.items() if k != "sha256"}) != manifest["sha256"]:
        raise ValueError("The benchmark manifest changed")
    if implementation_manifest(root) != manifest["implementation"]:
        raise ValueError("Implementation changed after freezing; start a new paired experiment")
    if dataset != manifest["dataset"] or provider != manifest["provider"]:
        raise ValueError("Observation data or provider configuration changed after freezing")


def read_frozen_corpora(destination: Path, manifest: dict) -> dict[str, list[dict]]:
    """Read evaluator-only files and reject substitutions before any turn runs."""
    if (
        digest({key: value for key, value in manifest.items() if key != "sha256"})
        != manifest["sha256"]
    ):
        raise ValueError("The benchmark manifest changed")
    corpora = {}
    for name in ("learning", "holdout_a", "holdout_b"):
        values = json.loads((destination / f"{name}.json").read_text())
        frozen = manifest["corpora"][name]
        if (
            not isinstance(values, list)
            or len(values) != frozen["count"]
            or digest(values) != frozen["sha256"]
        ):
            raise ValueError(f"Frozen corpus changed: {name}")
        corpora[name] = values
    return corpora
