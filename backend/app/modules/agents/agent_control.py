"""Governed collaboration over durable journal turns and participant mailboxes."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.modules.agents.auto_planner import (
    authorized_candidates,
    matched_alias,
    rank_candidates,
    semantic_matches,
)
from app.modules.agents.harness_repository import TERMINAL, HarnessRepository
from app.modules.agents.identity import participant_id, participant_path
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools.redaction import is_credential_value


@dataclass(frozen=True)
class CollaborationLimits:
    max_agent_depth: int = 4
    max_concurrent_agents: int = 8
    max_total_agent_sessions: int = 32
    max_total_turns: int = 128
    max_total_tokens: int = 120_000
    max_wall_time: int = 600

    @classmethod
    def from_root(cls, root: dict) -> CollaborationLimits:
        configured = (root.get("payload") or {}).get("limits") or {}
        defaults = asdict(cls())
        return cls(
            **{
                key: max(1, min(int(configured.get(key, value)), value))
                for key, value in defaults.items()
            }
        )


def session_views(turns: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for turn in turns:
        grouped.setdefault(participant_id(turn), []).append(turn)
    sessions = []
    for session_id, history in grouped.items():
        history.sort(
            key=lambda row: (int(row.get("payload", {}).get("turn_number", 1)), row["run_id"])
        )
        active = next((row for row in history if row["status"] not in TERMINAL), None)
        current = active or history[-1]
        first = history[0]
        status = current["status"]
        if status == "running" and current.get("checkpoint", {}).get("waiting"):
            status = "waiting_for_agent"
        if status == "completed" and first.get("depth"):
            status = "idle"
        if first.get("payload", {}).get("session_cancelled"):
            status = "cancelled"
        sessions.append(
            {
                "agent_session_id": session_id,
                "root_session_id": first.get("root_run_id") or first["run_id"],
                "parent_agent_session_id": first.get("payload", {}).get("parent_agent_session_id")
                or first.get("parent_run_id"),
                "agent_path": participant_path(first).value,
                "agent_id": first["agent_id"],
                "agent_name": first.get("payload", {}).get("agent_name") or first["agent_id"],
                "status": status,
                "depth": first["depth"],
                "current_turn_id": current["run_id"],
                "turn_count": len(history),
                "summary": current.get("result_summary"),
                "evidence_tables": current.get("checkpoint", {}).get("evidence_tables", []),
                "evidence": current.get("checkpoint", {}).get("verified_evidence", {}),
                "objective": current["objective"],
                "last_active_at": current.get("updated_at"),
            }
        )
    for session in sessions:
        session["children_count"] = sum(
            other["parent_agent_session_id"] == session["agent_session_id"] for other in sessions
        )
    return sorted(sessions, key=lambda item: item["agent_path"])


class AgentControl:
    def __init__(
        self,
        repository: HarnessRepository,
        caller: dict,
        user: dict,
        on_wait: Callable[[bool], None] | None = None,
        enforce_lease: bool = False,
    ) -> None:
        self.repository = repository
        self.caller = caller
        self.user = user
        self.root_id = caller.get("root_run_id") or caller["run_id"]
        self.on_wait = on_wait
        self.enforce_lease = enforce_lease

    async def _tree(self) -> list[dict]:
        if (
            self.user.get("username") != self.caller["owner_name"]
            or self.user.get("active_role") != self.caller["role_name"]
            or self.user.get("session_id") != self.caller["session_id"]
            or int(self.user.get("security_context_version") or 1)
            != int(self.caller.get("security_version") or 1)
        ):
            raise ValueError("Collaboration security context changed")
        tree = await self.repository.tree(
            self.root_id, owner_name=self.caller["owner_name"], role_name=self.caller["role_name"]
        )
        if not tree or any(
            any(
                row.get(key) != self.caller.get(key)
                for key in (
                    "owner_name",
                    "role_name",
                    "thread_id",
                    "session_id",
                    "security_version",
                )
            )
            for row in tree
        ):
            raise ValueError("Collaboration crosses a security boundary")
        if self.enforce_lease:
            current = next((row for row in tree if row["run_id"] == self.caller["run_id"]), None)
            if not current or current["status"] != "running" or any(
                current.get(key) != self.caller.get(key) for key in ("lease_owner", "generation")
            ):
                raise ValueError("Collaboration worker lease was lost")
        return tree

    async def _target(self, target: str) -> tuple[dict, list[dict]]:
        tree = await self._tree()
        sessions = session_views(tree)
        for session in sessions:
            if target in {session["agent_session_id"], session["current_turn_id"]}:
                return next(
                    row for row in tree if row["run_id"] == session["current_turn_id"]
                ), tree
        if target.startswith(("/", ".")):
            path = participant_path(self.caller).resolve(target).value
            for session in sessions:
                if path == session["agent_path"]:
                    return next(
                        row for row in tree if row["run_id"] == session["current_turn_id"]
                    ), tree
        raise ValueError("Agent is not visible in this collaboration")

    async def discover_agents(self, capability: str = "", *, decision: Any = None) -> list[dict]:
        await self._tree()
        candidates = await authorized_candidates(self.user)
        matches = semantic_matches(capability, candidates)
        from app.modules.assistant.decision import rank_agents

        ranked = await rank_agents(
            decision, capability, rank_candidates(capability, candidates, matches),
            semantic_matches=matches,
        )
        return [
            {
                **candidate.prompt_view(),
                "semantic_matches": [
                    match for match in matches if match["agent_id"] == candidate.agent_id
                ],
                "dimension_matches": [
                    field["name"] for field in candidate.dimensions
                    if matched_alias(capability, field)
                ],
            }
            for candidate in ranked[:12]
        ]

    async def list_agents(self) -> list[dict]:
        return session_views(await self._tree())

    @staticmethod
    def _bounded_context(parent: dict, tree: list[dict], mode: str, explicit: str) -> str:
        if mode not in {"fresh", "parent_summary", "last_n_turns", "full"}:
            raise ValueError("Unknown context inheritance mode")
        history = [row for row in tree if participant_id(row) == participant_id(parent)]
        history.sort(key=lambda row: int(row.get("payload", {}).get("turn_number", 1)))
        inherited = []
        if mode == "parent_summary":
            inherited = [parent["objective"], parent.get("result_summary") or ""]
        elif mode in {"last_n_turns", "full"}:
            inherited = [
                text
                for row in (history[-3:] if mode == "last_n_turns" else history)
                for text in (row["objective"], row.get("result_summary") or "")
            ]
        content = "\n\n".join([*inherited, explicit])[-8000:]
        if contains_credential_shape(content) or is_credential_value(content):
            raise ValueError("Delegated context contains sensitive content")
        return content

    def _check_budget(self, tree: list[dict], *, spawn: bool) -> None:
        root = tree[0]
        if root["status"] in TERMINAL:
            raise ValueError("Collaboration has ended")
        actual = next((row for row in tree if row["run_id"] == self.caller["run_id"]), None)
        if actual is None or actual["status"] in TERMINAL:
            raise ValueError("Calling turn has ended")
        limits = CollaborationLimits.from_root(root)
        if len(tree) >= limits.max_total_turns:
            raise ValueError("Total turn budget exhausted")
        if spawn:
            if self.caller["depth"] >= limits.max_agent_depth:
                raise ValueError("Agent depth limit reached")
            if len(session_views(tree)) >= limits.max_total_agent_sessions:
                raise ValueError("Total agent session budget exhausted")
            if (
                sum(
                    row["status"] not in TERMINAL
                    and row["status"] != "waiting_for_turn"
                    and row["depth"] > 0
                    for row in tree
                )
                >= limits.max_concurrent_agents
            ):
                raise ValueError("Concurrent agent limit reached; wait for an active agent")
        spent = sum(
            int(row.get("prompt_tokens") or 0) + int(row.get("completion_tokens") or 0)
            for row in tree
        )
        if spent >= limits.max_total_tokens:
            raise ValueError("Collaboration token budget exhausted")

    async def spawn_agent(
        self,
        *,
        agent: str,
        task_name: str,
        objective: str,
        operation_id: str,
        context_mode: str = "parent_summary",
        context: str = "",
    ) -> dict:
        await self._tree()
        # Authorization is independent of ranking: a valid specialist may rank below the preview.
        candidates = await authorized_candidates(self.user)
        candidate = next((item for item in candidates if item.agent_id == agent), None)
        if candidate is None:
            named = [item for item in candidates if item.name.casefold() == agent.casefold()]
            if len(named) == 1:
                candidate = named[0]
        if candidate is None:
            raise ValueError(
                "Agent is unavailable or ambiguous. Use an exact agent_id from discovery."
            )
        agent = candidate.agent_id
        path = participant_path(self.caller).child(task_name)
        async with self.repository.admission_lock(self.root_id, self.caller["owner_name"]) as owned:
            tree = await self._tree()
            expected = str(
                uuid5(NAMESPACE_URL, f"nova:spawn:{self.caller['run_id']}:{operation_id}")
            )
            existing = next((row for row in tree if row["run_id"] == expected), None)
            if existing:
                if (
                    existing["agent_id"] != agent
                    or existing["objective"] != objective[:4000]
                    or participant_path(existing) != path
                ):
                    raise ValueError("Spawn operation id collision")
                return next(
                    item for item in session_views(tree) if item["agent_session_id"] == expected
                )
            if any(item["agent_path"] == path.value for item in session_views(tree)):
                raise ValueError("Task path already exists; use followup_task to reuse it")
            self._check_budget(tree, spawn=True)
            inherited = self._bounded_context(self.caller, tree, context_mode, context)
            await owned()
            child = await self.repository.spawn(
                parent=self.caller,
                agent_id=agent,
                objective=objective,
                operation_id=operation_id,
                context=inherited,
                agent_name=candidate.name,
                agent_path=path.value,
                context_mode=context_mode,
            )
            return session_views([child])[0]

    async def send_message(
        self,
        *,
        target: str,
        content: str,
        operation_id: str,
        origin: str = "agent",
        correlation_id: str | None = None,
        reply_to: str | None = None,
    ) -> dict:
        async with self.repository.admission_lock(self.root_id, self.caller["owner_name"]) as owned:
            recipient, tree = await self._target(target)
            if tree[0]["status"] in TERMINAL or any(
                item["agent_session_id"] == participant_id(recipient)
                and item["status"] == "cancelled"
                for item in session_views(tree)
            ):
                raise ValueError("This collaboration or participant has ended")
            await owned()
            message_id = await self.repository.send(
                sender=self.caller,
                recipient={**recipient, "run_id": participant_id(recipient)},
                operation_id=operation_id,
                message_type="message",
                content=content,
                origin=origin,
                correlation_id=correlation_id,
                reply_to=reply_to,
            )
        return {"message_id": message_id, "delivery_mode": "QUEUE_ONLY"}

    async def followup_task(self, *, target: str, task: str, operation_id: str) -> dict:
        async with self.repository.admission_lock(self.root_id, self.caller["owner_name"]) as owned:
            recipient, tree = await self._target(target)
            if not recipient["depth"] or any(
                item["agent_session_id"] == participant_id(recipient)
                and item["status"] == "cancelled"
                for item in session_views(tree)
            ):
                raise ValueError("This agent cannot receive a follow-up")
            session_id = participant_id(recipient)
            turn_id = str(
                uuid5(NAMESPACE_URL, f"nova:followup:{self.caller['run_id']}:{operation_id}")
            )
            existing = next((row for row in tree if row["run_id"] == turn_id), None)
            if existing:
                if participant_id(existing) != session_id or existing["objective"] != task:
                    raise ValueError("Follow-up operation id collision")
                return {
                    "agent_session_id": session_id,
                    "turn_id": turn_id,
                    "delivery_mode": "TRIGGER_TURN",
                }
            self._check_budget(tree, spawn=False)
            if (
                not task.strip()
                or len(task) > 4000
                or contains_credential_shape(task)
                or is_credential_value(task)
            ):
                raise ValueError("Invalid follow-up objective")
            history = [row for row in tree if participant_id(row) == session_id]
            limits = CollaborationLimits.from_root(tree[0])
            active_count = sum(
                row["depth"] > 0
                and row["status"] not in TERMINAL
                and row["status"] != "waiting_for_turn"
                for row in tree
            )
            await owned()
            trigger_id = await self.repository.send(
                sender=self.caller,
                recipient={**recipient, "run_id": session_id},
                operation_id="task:" + operation_id,
                message_type="control",
                content=task,
                delivery_mode="TRIGGER_TURN",
            )
            await self.repository.create_followup(
                recipient,
                turn_id=turn_id,
                objective=task,
                turn_number=len(history) + 1,
                trigger_turn_id=self.caller["run_id"],
                trigger_message_id=trigger_id,
                blocked=any(row["status"] not in TERMINAL for row in history)
                or active_count >= limits.max_concurrent_agents,
            )
            return {
                "agent_session_id": session_id,
                "turn_id": turn_id,
                "delivery_mode": "TRIGGER_TURN",
            }

    async def interrupt_agent(
        self, *, target: str, cancel: bool = False, subtree: bool = False
    ) -> dict:
        async with self.repository.admission_lock(self.root_id, self.caller["owner_name"]) as owned:
            await owned()
            return await self._interrupt_agent(target=target, cancel=cancel, subtree=subtree)

    async def _interrupt_agent(self, *, target: str, cancel: bool, subtree: bool) -> dict:
        recipient, tree = await self._target(target)
        if not recipient["depth"] or participant_id(recipient) == participant_id(self.caller):
            raise ValueError("Cannot interrupt the root or the calling agent")
        path = participant_path(recipient)
        changed = []
        if cancel:
            for row in tree:
                if row["run_id"] == participant_id(row) and (
                    participant_id(row) == participant_id(recipient)
                    or (subtree and path.is_ancestor_of(participant_path(row)))
                ):
                    await self.repository.cancel_session(row)
                    await self.repository.event(
                        self.root_id, row["run_id"], "agent_session_cancelled", {}
                    )
        for row in tree:
            selected = participant_id(row) == participant_id(recipient) or (
                subtree and path.is_ancestor_of(participant_path(row))
            )
            if selected and row["status"] not in TERMINAL:
                status = "cancelled" if cancel else "interrupted"
                if await self.repository.transition(
                    row["run_id"], from_status=row["status"], to_status=status
                ):
                    await self.repository.ensure_terminal_event(
                        self.root_id, {**row, "status": status}
                    )
                    changed.append(row["run_id"])
        await self.repository.audit_interrupt(self.caller, recipient, cancel=cancel)
        return {"turn_ids": changed, "status": "cancelled" if cancel else "interrupted"}

    async def wait_agent(
        self, *, targets: list[str], condition: str = "all", timeout: float = 30
    ) -> dict:
        current = await self.repository.get(self.caller["run_id"])
        if current and current["status"] == "running":
            await self.repository.transition(
                current["run_id"],
                from_status="running",
                to_status="running",
                lease_owner=self.caller["lease_owner"],
                generation=self.caller["generation"],
                checkpoint={**current.get("checkpoint", {}), "waiting": True},
            )
        if self.on_wait:
            self.on_wait(True)
        await self.repository.event(
            self.root_id, self.caller["run_id"], "agent_waiting", {"reason": "collaboration"}
        )
        try:
            return await self._wait_agent(targets=targets, condition=condition, timeout=timeout)
        finally:
            if self.on_wait:
                self.on_wait(False)
            current = await self.repository.get(self.caller["run_id"])
            if current and current["status"] == "running":
                await self.repository.transition(
                    current["run_id"],
                    from_status="running",
                    to_status="running",
                    lease_owner=self.caller["lease_owner"],
                    generation=self.caller["generation"],
                    checkpoint={**current.get("checkpoint", {}), "waiting": False},
                )
                await self.repository.event(
                    self.root_id, self.caller["run_id"], "agent_resumed", {}
                )

    async def _wait_agent(self, *, targets: list[str], condition: str, timeout: float) -> dict:
        if condition not in {"any", "all"} or not 0 <= timeout <= 60 or len(targets) > 32:
            raise ValueError("Invalid wait condition or timeout")
        ids = {participant_id((await self._target(target))[0]) for target in targets}
        if participant_id(self.caller) in ids:
            raise ValueError("An agent cannot wait for itself")
        for target in targets:
            participant, _ = await self._target(target)
            if participant_path(participant).is_ancestor_of(participant_path(self.caller)):
                raise ValueError("An agent cannot wait for an ancestor to finish")
        deadline = time.monotonic() + timeout
        # Subscribe before inspecting durable state to close the check/subscribe race.
        async with self.repository.notifications(self.root_id) as wake:
            while True:
                tree = await self._tree()
                caller = next(row for row in tree if row["run_id"] == self.caller["run_id"])
                if caller["status"] in TERMINAL:
                    return {"reason": caller["status"], "agents": []}
                sessions = [item for item in session_views(tree) if item["agent_session_id"] in ids]
                finished = [item for item in sessions if item["status"] in {"idle", *TERMINAL}]
                if sessions and (
                    len(finished) == len(sessions) if condition == "all" else bool(finished)
                ):
                    return {"reason": "completed", "agents": finished}
                if await self.repository.pending_messages(participant_id(self.caller)):
                    return {"reason": "message", "agents": sessions}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"reason": "timeout", "agents": sessions}
                await wake(remaining)


def operation_key(turn_id: str, name: str, arguments: dict[str, Any]) -> str:
    value = json.dumps([turn_id, name, arguments], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()
