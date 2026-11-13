"""Fail acceptance when PostgreSQL logs contain synthetic credential markers."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_PATTERNS = {
    "credential_canary": re.compile(
        r"synthetic[-_a-z0-9]*(?:secret|credential|canary)|"
        r"synthetic-(?:parameter-key|header)|"
        r"(?:secret|private-header|parameter-secret)-canary", re.IGNORECASE,
    ),
    "encrypted_credential": re.compile(r"\bgAAAA[A-Za-z0-9_-]{40,}"),
}


def scan(text: str) -> dict:
    matches = {name: len(pattern.findall(text)) for name, pattern in _PATTERNS.items()}
    lines = len(text.splitlines())
    return {"passed": lines > 0 and not any(matches.values()), "lines": lines, "matches": matches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", type=Path)
    args = parser.parse_args()
    report = scan(args.logs.read_text())
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
