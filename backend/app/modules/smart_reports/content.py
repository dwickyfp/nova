"""Credential-free report references in persisted and streamed assistant content."""

from pydantic import Field

from app.modules.smart_reports.schemas import Contract, Identifier


class ReportBlock(Contract):
    report_id: Identifier
    draft_id: Identifier | None = None
    draft_revision: int | None = Field(default=None, ge=1)
    published_revision: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=256)
    fallback: str = Field(
        default="Open the report to view current authorized data.", max_length=1000
    )


def public_report_block(value: dict) -> dict:
    return ReportBlock.model_validate(
        {k: v for k, v in value.items() if k in ReportBlock.model_fields}
    ).model_dump(mode="json")
