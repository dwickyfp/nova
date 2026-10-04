"""Workflow participation identifies evidence without granting source access."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WorkflowProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: str | None = Field(default=None, min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    root_run_id: str | None = Field(default=None, min_length=1, max_length=128)


def workflow_provenance(context) -> dict:
    return WorkflowProvenance(
        mission_id=context.mission_id,
        run_id=context.run_id,
        root_run_id=getattr(context, "root_run_id", None) or context.run_id,
    ).model_dump(mode="json")
