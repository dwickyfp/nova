"""Agent Studio timestamps must carry UTC on the wire.

Metadata stores naive UTC. Serialized without a zone, the browser parses the
value as local time: an agent created at 13:45 in Jakarta showed "6:45 AM".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

from app.modules.agents.schemas import (
    AccessCheckResponse,
    AgentRoleView,
    AgentView,
    SkillView,
)

NAIVE_UTC = datetime(2026, 11, 13, 6, 45, 45)


def _agent(**times: datetime) -> AgentView:
    return AgentView(
        agent_id="a1",
        owner_name="nova_admin",
        name="QA Test Agent",
        created_at=times.get("created_at", NAIVE_UTC),
        updated_at=times.get("updated_at", NAIVE_UTC),
    )


def test_naive_agent_times_serialize_as_utc():
    payload = json.loads(_agent().model_dump_json())
    for field in ("created_at", "updated_at"):
        assert payload[field].endswith("Z")
        assert datetime.fromisoformat(payload[field]) == NAIVE_UTC.replace(tzinfo=UTC)


def test_aware_agent_time_keeps_its_instant():
    jakarta = timezone(timedelta(hours=7))
    local = NAIVE_UTC.replace(tzinfo=UTC).astimezone(jakarta)
    payload = json.loads(_agent(updated_at=local).model_dump_json())
    assert payload["updated_at"] == "2026-11-13T06:45:45Z"


def test_optional_and_other_agent_views_serialize_as_utc():
    role = json.loads(
        AgentRoleView(role_name="ACCOUNTADMIN", verified_at=NAIVE_UTC).model_dump_json()
    )
    assert role["verified_at"].endswith("Z")
    assert AgentRoleView(role_name="ACCOUNTADMIN").verified_at is None

    skill = SkillView(
        skill_id="s1",
        owner_name="nova_admin",
        name="monthly-review",
        created_at=NAIVE_UTC,
        updated_at=NAIVE_UTC,
    )
    assert json.loads(skill.model_dump_json())["created_at"].endswith("Z")

    check = AccessCheckResponse(
        role_name="ACCOUNTADMIN", all_granted=True, items=[], checked_at=NAIVE_UTC
    )
    assert json.loads(check.model_dump_json())["checked_at"].endswith("Z")
