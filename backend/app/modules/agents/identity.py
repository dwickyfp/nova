"""Stable participant identities; run IDs identify individual execution turns."""

from __future__ import annotations

import re
from dataclasses import dataclass

SMART_AGENT_ID = "__smart__"
LEGACY_AUTO_AGENT_ID = "__auto__"
SMART_AGENT_IDS = frozenset({SMART_AGENT_ID, LEGACY_AUTO_AGENT_ID})
_SEGMENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class AgentPath:
    value: str = "/root"

    def __post_init__(self) -> None:
        parts = self.value.split("/")
        if (
            len(self.value) > 512
            or parts[:2] != ["", "root"]
            or any(not _SEGMENT.fullmatch(part) for part in parts[2:])
        ):
            raise ValueError("Invalid agent path")

    @property
    def depth(self) -> int:
        return self.value.count("/") - 1

    def parent(self) -> AgentPath | None:
        return AgentPath(self.value.rsplit("/", 1)[0]) if self.depth else None

    def root(self) -> AgentPath:
        return AgentPath()

    def child(self, name: str) -> AgentPath:
        if not _SEGMENT.fullmatch(name):
            raise ValueError("Task name must contain lowercase letters, digits, _ or -")
        return AgentPath(f"{self.value}/{name}")

    def is_ancestor_of(self, other: AgentPath) -> bool:
        return other.value.startswith(self.value + "/")

    def is_descendant_of(self, other: AgentPath) -> bool:
        return other.is_ancestor_of(self)

    def resolve(self, target: str) -> AgentPath:
        if target.startswith("/"):
            return AgentPath(target)
        current = self
        for segment in target.split("/"):
            if segment == ".":
                continue
            if segment == "..":
                parent = current.parent()
                if parent is None:
                    raise ValueError("Agent path escapes the collaboration")
                current = parent
            else:
                current = current.child(segment)
        return current


def participant_id(turn: dict) -> str:
    return str((turn.get("payload") or {}).get("agent_session_id") or turn["run_id"])


def participant_path(turn: dict) -> AgentPath:
    payload = turn.get("payload") or {}
    return AgentPath(
        payload.get("agent_path")
        or ("/root" if not turn.get("depth") else f"/root/{turn['run_id']}")
    )
