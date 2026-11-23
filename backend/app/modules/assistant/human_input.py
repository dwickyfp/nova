"""Human-in-the-loop requests: the agent pauses, asks its user, and resumes.

One contract covers every kind of request a turn can make of the person who
asked it:

* ``approval`` — a proposed action (a change to data or configuration, a role
  or session change) waits for Allow once or Deny. Read-only calls never ask.
  The loop builds it from the call; the model never asks for approval itself.
* ``question`` — a clarifying question with suggested answers and/or free text.
* ``form`` — a small form generated from a JSON Schema subset the model supplies.
* ``choice`` — pick one of two to six described options.

The model asks with the ``request_human_input`` tool. The loop validates the
request here, publishes it (``input_required``), waits for the answer through
the shared consent broker and publishes the outcome (``input_resolved``), so the
event journal replays the same request and answer. An answer is accepted only
from the login, active role and security-context version that opened the
request, for the run and conversation it belongs to, and it is validated again
here: the browser's validation is a convenience, not a boundary.

Nothing in a request or an answer carries credentials; the strings are bounded
and an action's description is the tool's redacted preview.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

TOOL_NAME = "request_human_input"

KINDS = ("approval", "question", "form", "choice")
MODEL_KINDS = ("question", "form", "choice")

#: How long a question, form or choice waits before it expires. Approvals keep
#: the loop's consent timeout.
INPUT_TIMEOUT_SECONDS = 600.0
#: Requests one turn may make of its user; more is a loop, not a conversation.
MAX_REQUESTS_PER_TURN = 4

MAX_TITLE = 300
MAX_DETAIL = 800
MAX_CHOICE_TEXT = 120
MAX_CHOICES = 6
MAX_OPTIONS = 6
MIN_OPTIONS = 2
MAX_FIELDS = 8
MAX_TEXT_ANSWER = 1000
MAX_ENUM = 50
MAX_BADGES = 4

#: Terminal states of a request.
ANSWERED = "answered"
DECLINED = "declined"
EXPIRED = "expired"
CANCELLED = "cancelled"
ALLOWED = "allowed"
DENIED = "denied"

_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_OPTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class HumanInputError(ValueError):
    """A request or an answer that does not satisfy the contract."""


@dataclass
class HumanAnswer:
    """A validated answer to a question, form or choice, or a declined request."""

    status: str  # ANSWERED or DECLINED
    value: dict[str, Any] = field(default_factory=dict)
    summary: str = ""


# ── request normalization ──────────────────────────────────────────────────


def _text(value: Any, limit: int, *, name: str, required: bool = False) -> str:
    if value is None:
        if required:
            raise HumanInputError(f"{name} is required.")
        return ""
    if not isinstance(value, str):
        raise HumanInputError(f"{name} must be text.")
    cleaned = " ".join(value.split()) if limit <= MAX_TITLE else value.strip()
    if required and not cleaned:
        raise HumanInputError(f"{name} is required.")
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def _field(name: str, schema: Any) -> dict[str, Any]:
    if not _FIELD_NAME.match(name):
        raise HumanInputError(f"Field name {name!r} must be a short identifier.")
    if not isinstance(schema, dict):
        raise HumanInputError(f"Field {name} must be a schema object.")
    title = _text(schema.get("title") or name.replace("_", " ").capitalize(), 80, name="title")
    description = _text(schema.get("description"), 200, name="description")
    kind = schema.get("type")
    out: dict[str, Any] = {"name": name, "title": title}
    if description:
        out["description"] = description
    if kind == "string":
        enum = schema.get("enum")
        if enum is not None:
            if not isinstance(enum, list) or not 1 <= len(enum) <= MAX_ENUM:
                raise HumanInputError(f"Field {name} needs 1 to {MAX_ENUM} values.")
            values = [_text(item, MAX_CHOICE_TEXT, name="value", required=True) for item in enum]
            if len(set(values)) != len(values):
                raise HumanInputError(f"Field {name} repeats a value.")
            out.update(widget="select", values=values)
        elif schema.get("format") == "date":
            out["widget"] = "date"
        else:
            out.update(widget="text", max_length=min(int(schema.get("maxLength") or 200), 500))
        default = schema.get("default")
        if isinstance(default, str):
            out["default"] = default[:500]
    elif kind in {"number", "integer"}:
        out["widget"] = kind
        for bound in ("minimum", "maximum"):
            value = schema.get(bound)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[bound] = value
        if "minimum" in out and "maximum" in out and out["minimum"] > out["maximum"]:
            raise HumanInputError(f"Field {name} has minimum above maximum.")
        default = schema.get("default")
        if isinstance(default, (int, float)) and not isinstance(default, bool):
            out["default"] = default
    elif kind == "boolean":
        out["widget"] = "toggle"
        if isinstance(schema.get("default"), bool):
            out["default"] = schema["default"]
    elif kind == "object" and schema.get("format") == "date-range":
        out["widget"] = "date_range"
    else:
        raise HumanInputError(
            f"Field {name} has an unsupported type. Use string (optionally enum or "
            "format date), number, integer, boolean, or object with format date-range."
        )
    return out


def _form_fields(schema: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise HumanInputError("fields must be a JSON Schema object with properties.")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not 1 <= len(properties) <= MAX_FIELDS:
        raise HumanInputError(f"fields needs 1 to {MAX_FIELDS} properties.")
    fields = [_field(str(name), spec) for name, spec in properties.items()]
    names = {item["name"] for item in fields}
    required = schema.get("required") or []
    if not isinstance(required, list) or any(item not in names for item in required):
        raise HumanInputError("fields.required names a field that does not exist.")
    return fields, [str(item) for item in required]


def _options(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not MIN_OPTIONS <= len(raw) <= MAX_OPTIONS:
        raise HumanInputError(f"options needs {MIN_OPTIONS} to {MAX_OPTIONS} entries.")
    options: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise HumanInputError("Each option must be an object with a title.")
        option_id = str(item.get("id") or f"option_{index + 1}")
        if not _OPTION_ID.match(option_id):
            raise HumanInputError(f"Option id {option_id!r} must be a short identifier.")
        option: dict[str, Any] = {
            "id": option_id,
            "title": _text(item.get("title"), MAX_CHOICE_TEXT, name="option title", required=True),
        }
        description = _text(item.get("description"), 400, name="option description")
        if description:
            option["description"] = description
        recommended = item.get("recommended") is True
        badges = item.get("badges")
        if isinstance(badges, list):
            # The card marks the recommended option itself; a badge saying so
            # again would show twice.
            kept = [
                _text(badge, 40, name="badge") for badge in badges[:MAX_BADGES]
                if isinstance(badge, str) and badge.strip()
                and badge.strip().lower() not in _RECOMMENDED_BADGES
            ]
            if kept:
                option["badges"] = kept
            if len(kept) < len([b for b in badges[:MAX_BADGES] if isinstance(b, str) and b.strip()]):
                recommended = True
        if recommended:
            option["recommended"] = True
        options.append(option)
    if len({item["id"] for item in options}) != len(options):
        raise HumanInputError("Option ids must be unique.")
    return options


_RECOMMENDED_BADGES = frozenset({"recommended", "suggested", "best", "best option"})


def normalize_request(arguments: dict[str, Any]) -> dict[str, Any]:
    """The request the user sees, built from the model's tool arguments.

    Raises:
        HumanInputError: the arguments do not describe a valid request; the
            message is safe to return to the model for one repair.
    """
    kind = arguments.get("kind")
    if kind not in MODEL_KINDS:
        raise HumanInputError("kind must be question, form or choice.")
    request: dict[str, Any] = {
        "kind": kind,
        "title": _text(arguments.get("title"), MAX_TITLE, name="title", required=True),
    }
    detail = _text(arguments.get("detail"), MAX_DETAIL, name="detail")
    if detail:
        request["detail"] = detail
    if kind == "question":
        raw = arguments.get("choices") or []
        if not isinstance(raw, list):
            raise HumanInputError("choices must be a list of short answers.")
        choices = list(dict.fromkeys(
            _text(item, MAX_CHOICE_TEXT, name="choice", required=True) for item in raw[:MAX_CHOICES]
        ))
        free_text = arguments.get("allow_free_text")
        allow_free_text = True if free_text is None else bool(free_text)
        if not choices and not allow_free_text:
            raise HumanInputError("A question needs choices, free text, or both.")
        request.update(choices=choices, allow_free_text=allow_free_text)
    elif kind == "form":
        fields, required = _form_fields(arguments.get("fields"))
        request.update(fields=fields, required=required)
        submit = _text(arguments.get("submit_label"), 40, name="submit_label")
        if submit:
            request["submit_label"] = submit
    else:
        request["options"] = _options(arguments.get("options"))
    return request


# ── answers ────────────────────────────────────────────────────────────────


def _date(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _DATE.match(value):
        raise HumanInputError(f"{name} must be a date (YYYY-MM-DD).")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HumanInputError(f"{name} must be a real date.") from None
    return value


def _field_value(spec: dict[str, Any], value: Any) -> Any:
    title = spec["title"]
    widget = spec["widget"]
    if widget == "select":
        if value not in spec["values"]:
            raise HumanInputError(f"{title}: choose one of the listed values.")
        return value
    if widget == "text":
        if not isinstance(value, str):
            raise HumanInputError(f"{title} must be text.")
        if len(value) > spec.get("max_length", 200):
            raise HumanInputError(f"{title} is too long.")
        return value.strip()
    if widget == "date":
        return _date(value, title)
    if widget == "date_range":
        if not isinstance(value, dict):
            raise HumanInputError(f"{title} needs a start and an end date.")
        start = _date(value.get("start"), f"{title} start")
        end = _date(value.get("end"), f"{title} end")
        if start > end:
            raise HumanInputError(f"{title}: the start date is after the end date.")
        return {"start": start, "end": end}
    if widget in {"number", "integer"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HumanInputError(f"{title} must be a number.")
        if widget == "integer" and int(value) != value:
            raise HumanInputError(f"{title} must be a whole number.")
        if "minimum" in spec and value < spec["minimum"]:
            raise HumanInputError(f"{title} must be at least {spec['minimum']}.")
        if "maximum" in spec and value > spec["maximum"]:
            raise HumanInputError(f"{title} must be at most {spec['maximum']}.")
        return int(value) if widget == "integer" else value
    if widget == "toggle":
        if not isinstance(value, bool):
            raise HumanInputError(f"{title} must be on or off.")
        return value
    raise HumanInputError(f"{title} cannot be answered.")


def _display(spec: dict[str, Any], value: Any) -> str:
    if spec["widget"] == "toggle":
        return "on" if value else "off"
    if spec["widget"] == "date_range":
        return f"{value['start']} to {value['end']}"
    return str(value)


def _clip(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def validate_answer(request: dict[str, Any], answer: Any) -> HumanAnswer:
    """Check an answer against its request and summarize it in one line.

    ``answer`` is ``{"text": ...}`` for a question, ``{"values": {...}}`` for a
    form and ``{"option_id": ...}`` for a choice.
    """
    if not isinstance(answer, dict):
        raise HumanInputError("The answer is missing.")
    kind = request.get("kind")
    if kind == "question":
        text = answer.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HumanInputError("Write an answer or pick a suggestion.")
        text = text.strip()
        if len(text) > MAX_TEXT_ANSWER:
            raise HumanInputError("The answer is too long.")
        if not request.get("allow_free_text") and text not in request.get("choices", []):
            raise HumanInputError("Pick one of the suggested answers.")
        return HumanAnswer(ANSWERED, {"text": text}, f"You answered: {_clip(text)}")
    if kind == "choice":
        option_id = answer.get("option_id")
        option = next(
            (item for item in request.get("options", []) if item["id"] == option_id), None,
        )
        if option is None:
            raise HumanInputError("Pick one of the options.")
        return HumanAnswer(
            ANSWERED, {"option_id": option["id"], "title": option["title"]},
            f"You chose: {_clip(option['title'])}",
        )
    if kind == "form":
        raw = answer.get("values")
        if not isinstance(raw, dict):
            raise HumanInputError("The form values are missing.")
        specs = {item["name"]: item for item in request.get("fields", [])}
        unknown = set(raw) - set(specs)
        if unknown:
            raise HumanInputError("The form has values for fields it does not ask for.")
        values: dict[str, Any] = {}
        for name, spec in specs.items():
            value = raw.get(name)
            if value is None or value == "":
                if name in request.get("required", []):
                    raise HumanInputError(f"{spec['title']} is required.")
                continue
            values[name] = _field_value(spec, value)
        shown = ", ".join(
            f"{specs[name]['title']} {_display(specs[name], value)}"
            for name, value in values.items()
        )
        summary = f"You set: {shown}" if shown else "You submitted the form empty"
        return HumanAnswer(ANSWERED, {"values": values}, _clip(summary))
    raise HumanInputError("This request cannot be answered this way.")


def declined() -> HumanAnswer:
    return HumanAnswer(DECLINED, {}, "You skipped this question")


def tool_result(status: str, answer: HumanAnswer | None) -> dict[str, Any]:
    """The tool result the model reads after its request settles."""
    if status == ANSWERED and answer is not None:
        return {
            "ok": True,
            "tool": TOOL_NAME,
            "data": {"status": ANSWERED, "answer": answer.value, "summary": answer.summary},
        }
    detail = {
        DECLINED: "The user skipped this request. Continue with a clearly stated, "
                  "conservative assumption, or say what is still needed. Do not ask again.",
        EXPIRED: "The user did not answer in time. Continue with a clearly stated, "
                 "conservative assumption, or say what is still needed. Do not ask again.",
    }.get(status, "The request was not answered.")
    return {"ok": True, "tool": TOOL_NAME, "data": {"status": status, "summary": detail}}


# ── the tool the model calls ───────────────────────────────────────────────

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": list(MODEL_KINDS),
            "description": (
                "question: one clarifying question with suggested answers; form: a few "
                "typed values (dates, a period, a number, a value from a known list); "
                "choice: pick one of 2-6 described options such as analysis plans, "
                "matching semantic views or chart variants."
            ),
        },
        "title": {"type": "string", "description": "The question in the user's language."},
        "detail": {
            "type": "string",
            "description": "One sentence: why the answer changes the result.",
        },
        "choices": {
            "type": "array",
            "items": {"type": "string"},
            "description": "question: up to 6 short suggested answers.",
        },
        "allow_free_text": {
            "type": "boolean",
            "description": "question: whether the user may type their own answer (default true).",
        },
        "fields": {
            "type": "object",
            "description": (
                "form: a JSON Schema object. properties (1-8) may be string (with enum for "
                "a list of known values, or format date), number, integer (minimum, "
                "maximum), boolean, or {\"type\": \"object\", \"format\": \"date-range\"}; "
                "each with a title. List required field names in required."
            ),
        },
        "submit_label": {"type": "string", "description": "form: the submit button label."},
        "options": {
            "type": "array",
            "description": (
                "choice: 2-6 options, each {id, title, description, badges?, recommended?}."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "badges": {"type": "array", "items": {"type": "string"}},
                    "recommended": {"type": "boolean"},
                },
                "required": ["title"],
            },
        },
    },
    "required": ["kind", "title"],
}


class RequestHumanInputTool:
    """Pause the turn and ask the user; the loop delivers the answer as the result.

    The loop handles this tool itself (it owns the pause, the broker and the
    events), so :meth:`run` is only reached when a caller has no person to ask.
    """

    name = TOOL_NAME
    human_input = True
    description = (
        "Ask the user who asked this question and wait for the answer before going on. "
        "Use it only when the answer materially changes the result and cannot be "
        "inferred: an ambiguous metric (gross or net revenue), a missing period, a "
        "choice between analysis plans or matching data sources. Ask once, with "
        "concrete suggestions; never to confirm something already stated, and never "
        "for permission to run a query (Nova asks for approval itself). When you "
        "need the user's choice, ask with this tool rather than in your answer text, "
        "then continue the work with the answer."
    )
    parameters = _PARAMETERS
    classification: ToolClassification = "read_only"
    #: Asking the person is not an action on their data.
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        return ""

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        return ToolOutcome(
            ok=False, summary="", error="No one can answer a question in this run.",
            error_class="HUMAN_INPUT_UNAVAILABLE", recoverable=True,
            safe_detail="Continue without asking; state any assumption you make.",
        )


request_human_input_tool = RequestHumanInputTool()


# ── approvals ──────────────────────────────────────────────────────────────

#: Why an approval is asked, by the loop's reason code. Read-only calls never ask:
#: the caller's role already governs what they read.
APPROVAL_REASONS = ("changes_data", "changes_role")


def approval_reason(classification: str) -> str:
    """Why this call waits: it switches the role or session, or it changes something."""
    return "changes_role" if classification == "session_change" else "changes_data"


def approval_request(
    tool: Any,
    invocation: ToolInvocation,
    *,
    classification: str,
    preview: str,
    role: str,
    reason: str,
) -> dict[str, Any]:
    """The approval card for one proposed action: what it does, as whom, and why ask.

    ``action`` is the tool's own plain description of this call (its redacted
    preview, such as "Schedule 'Weekly revenue' (cron: 0 8 * * 1)"). Nothing
    here reads data.
    """
    return {
        "kind": "approval",
        "title": (
            "Allow this change to your session?" if classification == "session_change"
            else "Allow this action?"
        ),
        "classification": classification,
        "reason": reason,
        "role": role,
        "action": preview,
    }


def approval_summary(status: str) -> str:
    return {
        ALLOWED: "You allowed this action",
        DENIED: "You denied this action",
        EXPIRED: "The approval request expired",
        CANCELLED: "Stopped before you answered",
    }.get(status, "")
