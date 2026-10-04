"""Bounded, credential-free projections of specialist loop frames."""

from __future__ import annotations

import json
from typing import Any

from app.common.sql_guard import CredentialsRedactionError, redact_sql_credentials
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools.redaction import is_credential_value


def safe_public_text(value: Any, limit: int) -> str:
    try:
        safe = redact_sql_credentials(str(value or ""))
    except CredentialsRedactionError:
        return "[redacted]"
    if contains_credential_shape(safe) or is_credential_value(safe):
        return "[redacted]"
    return safe[:limit]


def _fields(source: dict[str, Any], sizes: dict[str, int]) -> dict[str, str]:
    return {name: safe_public_text(source.get(name), size) for name, size in sizes.items()}


def activity_from_frame(frame: str) -> dict[str, Any] | None:
    kind = frame.partition("\n")[0].removeprefix("event: ").strip()
    try:
        payload = json.loads(frame.split("data: ", 1)[1])
    except (IndexError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if kind == "mission_continuation":
        from pydantic import ValidationError

        from app.modules.agents.mission_schema import ContinuationDecision

        try:
            bounded = ContinuationDecision.model_validate(payload.get("continuation"))
        except ValidationError:
            return None
        return {"event_type": kind, "continuation": bounded.model_dump(mode="json")}
    if kind == "evidence_envelope":
        from pydantic import ValidationError

        from app.modules.intelligence.evidence import EvidenceEnvelope

        try:
            bounded = EvidenceEnvelope.model_validate(payload.get("payload"))
        except ValidationError:
            return None
        return {"event_type": kind, **_fields(payload, {"tool_call_id": 128, "tool_name": 80}),
                "payload": bounded.model_dump(mode="json")}
    if kind == "execution_time_context":
        from pydantic import ValidationError

        from app.modules.agents.mission_schema import ExecutionTimeContext

        raw = payload.get("payload") or {}
        fixed = raw.get("execution_time") or {}
        try:
            bounded = ExecutionTimeContext.model_validate({
                "execution_now": fixed.get("now"), "timezone": fixed.get("timezone"),
                "semantic": {"view_id": raw.get("semantic_view_id"),
                             "version": raw.get("semantic_version"),
                             "fingerprint": raw.get("semantic_fingerprint")},
                "plan_fingerprint": raw.get("validated_plan_fingerprint"),
                "current_window": fixed.get("current"), "baseline_window": fixed.get("baseline"),
                "warnings": fixed.get("warnings", []),
            })
        except ValidationError:
            return None
        return {"event_type": kind, **_fields(payload, {"tool_call_id": 128}),
                "payload": bounded.model_dump(mode="json")}
    if kind == "plan":
        steps = payload.get("steps")
        if not isinstance(steps, list):
            return None
        return {
            "event_type": kind,
            "steps": [
                _fields(step, {"id": 64, "text": 200, "status": 24})
                for step in steps[:8]
                if isinstance(step, dict)
            ],
        }
    if kind == "thinking":
        return {
            "event_type": kind,
            **_fields(payload, {"phase": 32, "text": 240, "status": 24}),
        }
    if kind == "tool_call":
        return {
            "event_type": kind,
            **_fields(
                payload,
                {
                    "tool_call_id": 64,
                    "tool_name": 80,
                    "skill_name": 80,
                    "sql_preview": 1200,
                    "classification": 32,
                    "status": 24,
                    "result_summary": 400,
                },
            ),
        }
    if kind == "tool_progress":
        return {
            "event_type": kind,
            **_fields(
                payload,
                {"tool_call_id": 64, "stage": 80, "text": 400, "sql_preview": 1200},
            ),
        }
    if kind == "tool_status":
        return {
            "event_type": kind,
            **_fields(payload, {"tool_call_id": 64, "status": 24}),
        }
    if kind == "tool_detail":
        return {
            "event_type": kind,
            **_fields(payload, {"tool_call_id": 64, "text": 1200}),
        }
    if kind == "error":
        return {
            "event_type": kind,
            **_fields(payload, {"code": 80, "message": 400}),
        }
    if kind == "done":
        return {
            "event_type": kind,
            **_fields(payload, {"finish_reason": 64}),
        }
    return None
