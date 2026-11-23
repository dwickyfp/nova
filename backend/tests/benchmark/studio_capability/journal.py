"""Fold one Studio protocol-v2 event journal into what the user saw.

This mirrors the client reducer (``frontend/src/features/studio/runtime/
turn-reducer.ts``) only as far as the benchmark needs: the ordered parts with
their final text or block, the tools and their end state, the human-input
requests, errors, usage and the timings of the first visible activity and the
first answer token. Each recorded event carries ``t`` (seconds since the
request was sent), added by the runner.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

#: Components an interactive (``ui``) program can place, as written in its source.
_UI_COMPONENT = re.compile(r"\b([A-Z][A-Za-z]+)\(")
_UI_CHART_TYPE = re.compile(r"\btype\s*[:=]\s*\"(bar|line|area|pie|donut|scatter)\"")
_VEGA_MARK = re.compile(r"\"mark\"\s*:\s*(?:\{\s*\"type\"\s*:\s*)?\"(\w+)\"")
_VEGA_KIND = {"bar": "bar", "line": "line", "area": "area", "arc": "pie", "point": "scatter",
              "circle": "scatter", "square": "scatter", "tick": "other", "text": "other"}


@dataclass
class Part:
    part_id: str
    type: str
    text: str = ""
    state: str = ""
    block: dict[str, Any] = field(default_factory=dict)
    tool_name: str = ""
    tool_status: str = ""
    preview: str = ""
    hitl_kind: str = ""
    hitl_request: dict[str, Any] = field(default_factory=dict)
    hitl_status: str = ""
    phase: str = ""
    opened_at: float | None = None


@dataclass
class TurnView:
    parts: list[Part] = field(default_factory=list)
    finish_reason: str | None = None
    message_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    run_id: str | None = None
    protocol_version: int | None = None
    first_event_s: float | None = None
    first_activity_s: float | None = None
    first_answer_token_s: float | None = None
    finished_s: float | None = None
    seq_gaps: int = 0
    duplicate_seq: int = 0

    # ── derived views ───────────────────────────────────────────────────

    @property
    def answer_text(self) -> str:
        return "\n\n".join(p.text for p in self.parts if p.type == "text" and p.text).strip()

    @property
    def tools(self) -> list[str]:
        return [p.tool_name for p in self.parts if p.type == "tool"]

    def tool_states(self) -> list[tuple[str, str]]:
        return [(p.tool_name, p.tool_status or p.state) for p in self.parts if p.type == "tool"]

    @property
    def hitl_kinds(self) -> list[str]:
        return [p.hitl_kind for p in self.parts if p.type == "hitl_request" and p.hitl_kind]

    def ui_components(self) -> list[str]:
        names: list[str] = []
        for part in self.parts:
            if part.type == "ui":
                names.extend(_UI_COMPONENT.findall(part.text))
        return names

    def chart_kinds(self) -> list[str]:
        kinds: list[str] = []
        for part in self.parts:
            if part.type == "chart":
                spec = str(part.block.get("chart_spec") or part.block.get("spec") or "")
                mark = _VEGA_MARK.search(spec)
                chart_type = str(part.block.get("chart_type") or "")
                kinds.append(chart_type or (_VEGA_KIND.get(mark.group(1), "other") if mark
                                            else "other"))
            elif part.type == "ui":
                if "Chart(" in part.text:
                    found = _UI_CHART_TYPE.findall(part.text)
                    kinds.extend(found or ["bar"])
        return kinds

    def elements(self) -> set[str]:
        """The UI element vocabulary the corpus uses for ``expect_ui``."""
        out: set[str] = set()
        if self.answer_text:
            out.add("text")
        types = {p.type for p in self.parts}
        components = set(self.ui_components())
        if "table" in types or "Table" in components:
            out.add("table")
        for kind in self.chart_kinds():
            out.add("chart")
            out.add(f"chart:{'pie' if kind == 'donut' else kind}")
        if "Kpi" in components:
            out.add("kpi")
        if components & {"GeoMap", "Map"}:
            out.add("map")
        if components & {"Slider", "Input", "Checkbox", "DatePicker", "Form"}:
            out.add("interactive")
        if "ui" in types:
            out.add("ui")
        if "citation" in types:
            out.add("citation")
        if "dashboard" in types:
            out.add("dashboard")
        for kind in self.hitl_kinds:
            out.add(f"hitl:{kind}")
        if self.suggestions:
            out.add("suggestions")
        return out

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        data["answer_text"] = self.answer_text
        data["tools"] = self.tools
        data["elements"] = sorted(self.elements())
        data["ui_components"] = sorted(set(self.ui_components()))
        data["chart_kinds"] = self.chart_kinds()
        return data


def fold(events: list[dict[str, Any]]) -> TurnView:
    """Reduce ``[{event, data, t}]`` into a :class:`TurnView`."""
    view = TurnView()
    parts: dict[str, Part] = {}
    last_seq: int | None = None
    seen: set[int] = set()
    for entry in events:
        name = str(entry.get("event") or "")
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        at = entry.get("t")
        seq = data.get("seq")
        if isinstance(seq, int):
            if seq in seen:
                view.duplicate_seq += 1
                continue
            seen.add(seq)
            if last_seq is not None and seq != last_seq + 1:
                view.seq_gaps += 1
            last_seq = seq
        if view.first_event_s is None and isinstance(at, int | float):
            view.first_event_s = float(at)
        if name == "run.started":
            view.protocol_version = data.get("protocol_version")
            view.run_id = data.get("run_id")
            if data.get("role"):
                view.roles.append(str(data["role"]))
        elif name == "part.start":
            part = Part(
                part_id=str(data.get("part_id")),
                type=str(data.get("type") or ""),
                block=data.get("block") if isinstance(data.get("block"), dict) else {},
                text=str(data.get("text") or "") if data.get("type") == "reasoning" else "",
                tool_name=str(data.get("tool_name") or ""),
                phase=str(data.get("phase") or data.get("label") or ""),
                opened_at=at if isinstance(at, int | float) else None,
            )
            if part.type == "hitl_request":
                part.hitl_kind = str(data.get("kind") or "")
            parts[part.part_id] = part
            view.parts.append(part)
            if view.first_activity_s is None and isinstance(at, int | float):
                view.first_activity_s = float(at)
        elif name == "part.delta":
            part = parts.get(str(data.get("part_id")))
            if part is not None:
                text = str(data.get("text") or "")
                part.text = text if data.get("replace") else part.text + text
                if (part.type == "text" and text.strip() and view.first_answer_token_s is None
                        and isinstance(at, int | float)):
                    view.first_answer_token_s = float(at)
        elif name == "part.end":
            part = parts.get(str(data.get("part_id")))
            if part is not None:
                part.state = str(data.get("state") or "")
                final = data.get("final")
                if isinstance(final, str) and part.type == "text":
                    part.text = final
        elif name == "tool.status":
            part = parts.get(str(data.get("part_id")))
            if part is not None:
                part.tool_status = str(data.get("status") or part.tool_status)
                if data.get("preview") and not part.preview:
                    part.preview = str(data["preview"])[:2000]
        elif name == "input.required":
            part = parts.get(str(data.get("part_id")))
            if part is not None:
                part.hitl_kind = str(data.get("kind") or part.hitl_kind)
                request = data.get("request")
                part.hitl_request = request if isinstance(request, dict) else {}
        elif name == "input.resolved":
            part = parts.get(str(data.get("part_id")))
            if part is not None:
                part.hitl_status = str(data.get("status") or "")
        elif name == "role.changed":
            view.roles.append(str(data.get("active_role") or ""))
        elif name == "suggestions":
            raw = data.get("suggestions")
            if isinstance(raw, list):
                view.suggestions = [str(item) for item in raw][:10]
        elif name == "run.error":
            view.errors.append({"code": data.get("code"),
                                "message": str(data.get("message"))[:300]})
        elif name == "run.finished":
            view.finish_reason = data.get("finish_reason")
            view.message_id = data.get("message_id")
            if isinstance(data.get("usage"), dict):
                view.usage = data["usage"]
            if isinstance(at, int | float):
                view.finished_s = float(at)
    # A text part's first delta may be its whole content when it is not streamed.
    if view.first_answer_token_s is None:
        for part in view.parts:
            if part.type == "text" and part.text.strip() and part.opened_at is not None:
                view.first_answer_token_s = part.opened_at
                break
    return view
