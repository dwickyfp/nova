from __future__ import annotations

import hashlib
from pathlib import Path


def source_snapshot() -> dict[str, str]:
    backend = Path(__file__).parents[3]
    snapshot = {}
    for name, root in {"application": backend / "app", "harness": Path(__file__).parent}.items():
        digest = hashlib.sha256()
        paths = sorted(path for path in root.rglob("*") if path.suffix in {".py", ".json", ".java"})
        for path in paths:
            digest.update(str(path.relative_to(backend)).encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        snapshot[name] = digest.hexdigest()
    digest = hashlib.sha256()
    configuration = [
        backend / "pyproject.toml",
        backend / "uv.lock",
        backend.parent / "docker/nova.yaml",
    ]
    for path in configuration:
        digest.update(path.name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    snapshot["configuration_files"] = digest.hexdigest()
    return snapshot
