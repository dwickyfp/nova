"""Fail CI when a user-data adapter gains a system/root execution shortcut."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USER_DATA_PATHS = (
    ROOT / "app/modules/assistant/tools",
    ROOT / "app/modules/agents/tools",
    ROOT / "app/modules/agents/semantic",
    ROOT / "app/modules/ml_engine/data",
)
FORBIDDEN = ("execute_system(", "system_conn(", "STARROCKS_ROOT_USER", "as_system=True")


def main() -> int:
    findings: list[str] = []
    for directory in USER_DATA_PATHS:
        for path in directory.rglob("*.py"):
            for line_number, line in enumerate(path.read_text().splitlines(), 1):
                if "nova-control-plane" in line:
                    continue
                if any(token in line for token in FORBIDDEN):
                    findings.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
    if findings:
        print("User-data code must use a delegated SecurityContext:")
        print("\n".join(findings))
        return 1
    print("No system/root shortcuts found in user-data adapters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
