"""Credential-free catalog contract for the shared assistant report tool."""

from app.modules.smart_reports.schemas import ReportSpecV1

DESCRIPTION = (
    "Create a persistent interactive Smart Report for a report or dashboard request. "
    "First use catalog to resolve published semantic sources. Create accepts a declarative "
    "ReportSpec, creates a private draft, and emits an interactive report in chat. "
    "Use context to read a current report selection by its view_session_id. "
    "This tool cannot publish or share."
)
_spec = ReportSpecV1.model_json_schema()
PARAMETERS = {
    "type": "object",
    "$defs": _spec.pop("$defs"),
    "properties": {
        "action": {"type": "string", "enum": ["catalog", "create", "context"]},
        "spec": _spec,
        "report_id": {"type": "string"},
        "view_session_id": {"type": "string"},
    },
    "required": ["action"],
    "additionalProperties": False,
}
