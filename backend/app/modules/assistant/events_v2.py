"""Studio stream protocol v2: ordered, typed parts with one sequence per run.

The shared loop still produces its established frames (``events.py``). A
Studio turn re-encodes them here into protocol v2 before numbering, journaling
and delivery, so the live stream, the run journal used for resume and the
journal stored on the assistant message are the same list of events.

Every v2 frame carries ``run_id`` and ``seq``; the route assigns ``seq`` when it
numbers the frame. ``part_id`` values are assigned here, in production order,
as ``<run_id>#<n>`` (distinct from content ids, ``<run_id>:<index>``), so a
replay of the stored journal yields the same ids as the live stream.

=====================  =====================================================
Event                  Payload
=====================  =====================================================
``run.started``        ``{protocol_version, role}``
``part.start``         ``{part_id, type, ...}`` opens one part
``part.delta``         ``{part_id, text, replace?}`` appends (or replaces) text
``part.end``           ``{part_id, state, final?}`` seals one part
``tool.status``        ``{part_id, status, preview?, progress?, detail?}``
``tool.evidence``      ``{part_id, kind, tool_name?, payload, workflow?}``
``input.required``     ``{part_id, request_id, kind, request, expires_at}``
``input.resolved``     ``{part_id, status, summary, answer?}``
``role.changed``       ``{active_role}``
``run.finished``       ``{message_id, finish_reason, usage?, source?}``
``run.error``          ``{code, message}``
``ping``               ``{}``
=====================  =====================================================

Part types are ``reasoning`` (``source: "nova"`` phase labels in this
protocol; provider reasoning is not read), ``text``, ``tool``, ``guard``,
``hitl_request`` and the authored content blocks ``table``, ``chart``,
``citation``, ``dashboard`` and ``ui``.

A ``hitl_request`` part is the turn asking its user (``kind`` approval,
question, form or choice). It opens where the request was made, is followed by
``input.required`` with the request, and seals after ``input.resolved``. An
approval part names the ``tool`` part it decides (``tool_part_id``). Side-effect events (``suggestions``, ``mission_updated``,
``mission_continuation``, ``dashboard_changed``, ``dashboard_activity``,
``client_action``, ``execution_time_context``, ``business_result``) keep their
established payloads and are numbered in the same sequence.

Threads stored before this protocol have no journal and replay from their
recorded steps (protocol 1, read-only).
"""

from __future__ import annotations

import json
from typing import Any

from app.modules.assistant import events

PROTOCOL_VERSION = 2

RUN_STARTED = "run.started"
PART_START = "part.start"
PART_DELTA = "part.delta"
PART_END = "part.end"
TOOL_STATUS = "tool.status"
TOOL_EVIDENCE = "tool.evidence"
INPUT_REQUIRED = "input.required"
INPUT_RESOLVED = "input.resolved"
ROLE_CHANGED = "role.changed"
RUN_FINISHED = "run.finished"
RUN_ERROR = "run.error"
PING = "ping"

#: Events that keep their v1 name and payload.
PASSTHROUGH = frozenset({
    "suggestions", "mission_updated", "mission_continuation",
    events.EVENT_DASHBOARD_CHANGED, events.EVENT_DASHBOARD_ACTIVITY,
    events.EVENT_CLIENT_ACTION, "execution_time_context", "business_result",
})

#: Frame names that exist only in protocol v1 Studio streams.
V1_ONLY = frozenset({
    events.EVENT_TEXT_DELTA, events.EVENT_THINKING, events.EVENT_PLAN, events.EVENT_TOOL_CALL,
    events.EVENT_TOOL_PROGRESS, events.EVENT_TOOL_STATUS, events.EVENT_TOOL_DETAIL,
    events.EVENT_CONTENT_BLOCK_DONE, events.EVENT_DONE, events.EVENT_ERROR, events.EVENT_TABLE,
    events.EVENT_CHART, events.EVENT_CITATION, "ui", "ui_delta", "dashboard", "guard",
    "role_changed", "evidence_health", "evidence_envelope",
    events.EVENT_INPUT_REQUIRED, events.EVENT_INPUT_RESOLVED,
})

#: Frames that mark a durable boundary: the route flushes its journal batch.
FLUSH_EVENTS = frozenset({
    INPUT_REQUIRED, INPUT_RESOLVED, TOOL_STATUS, ROLE_CHANGED, RUN_ERROR, RUN_FINISHED,
    "execution_time_context",
})

#: Events kept out of the journal stored on the message. A Mission projection is
#: reloaded from the Mission itself; a keep-alive has nothing to replay.
NOT_STORED = frozenset({"mission_updated", PING})

_TERMINAL_TOOL = frozenset({"done", "failed", "denied", "cancelled"})
_CONTENT_TYPES = {
    events.EVENT_TABLE: "table",
    events.EVENT_CHART: "chart",
    events.EVENT_CITATION: "citation",
    "dashboard": "dashboard",
}
#: Envelope fields the route owns; they are never copied into a part.
_ENVELOPE = frozenset({"run_id", "sequence", "seq"})


def parse_frame(frame: str) -> tuple[str, dict[str, Any]]:
    """Split one SSE frame into its event name and JSON object payload."""
    name = ""
    data: Any = {}
    for line in frame.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
            except ValueError:
                data = {}
    return name, data if isinstance(data, dict) else {}


def frame_sequence(frame: str) -> int:
    """The run sequence of a numbered frame: ``seq`` (v2) or ``sequence`` (v1)."""
    try:
        payload = json.loads(frame.split("data: ", 1)[1])
        value = payload["seq"] if "seq" in payload else payload["sequence"]
        if type(value) is not int:
            raise TypeError
        return value
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("The run event has no sequence.") from exc


def frame_name(frame: str) -> str:
    return frame.split("\n", 1)[0].removeprefix("event: ").strip()


def _position(body: dict[str, Any]) -> dict[str, Any]:
    """The authored position a content part keeps: index and server content id.

    ``content_id`` stays the block's identity for Dataviz publication and
    interactive-UI state; ``part_id`` orders it in the turn.
    """
    return {key: body[key] for key in ("content_index", "content_id") if body.get(key) is not None}


def _body(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in _ENVELOPE}


class StudioStreamEncoder:
    """Re-encode one run's v1 frames as protocol v2 frames.

    Stateful per run: it remembers which parts are open so a status, a delta or
    the end of a block reaches the part it belongs to. It never invents content;
    a frame it has no mapping for is dropped rather than passed on unnumbered.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._count = 0
        # Keyed by (originating run, tool call id): a Mission's nested runs share
        # this stream, and their call ids are only unique within each run.
        self._tools: dict[tuple[str, str], str] = {}
        self._tool_state: dict[tuple[str, str], str] = {}
        self._content: dict[str, str] = {}
        # Human-input parts by (originating run, request id).
        self._inputs: dict[tuple[str, str], str] = {}
        self._open: dict[str, str] = {}  # part_id -> type, in opening order
        self._reasoning: tuple[str, str, str] | None = None  # part_id, phase, text
        self._plan: str | None = None
        self.started = False
        self.finished = False

    # ── helpers ─────────────────────────────────────────────────────────

    def _part_id(self) -> str:
        part_id = f"{self.run_id}#{self._count}"
        self._count += 1
        return part_id

    @staticmethod
    def _frame(name: str, payload: dict[str, Any]) -> str:
        # Numbered by the route; ``format_sse`` would otherwise add a v1 sequence.
        return f"event: {name}\ndata: {json.dumps(payload, separators=(',', ':'), default=events._json_default)}\n\n"  # noqa: E501

    def _start(self, part_type: str, **fields: Any) -> tuple[str, str]:
        part_id = self._part_id()
        self._open[part_id] = part_type
        return part_id, self._frame(PART_START, {"part_id": part_id, "type": part_type, **fields})

    def _delta(self, part_id: str, text: Any) -> str:
        return self._frame(PART_DELTA, {"part_id": part_id, "text": str(text or "")})

    def _end(self, part_id: str, state: str = "done", final: Any = None) -> str:
        self._open.pop(part_id, None)
        payload: dict[str, Any] = {"part_id": part_id, "state": state}
        if final is not None:
            payload["final"] = final
        return self._frame(PART_END, payload)

    def _close_reasoning(self) -> list[str]:
        if self._reasoning is None:
            return []
        part_id = self._reasoning[0]
        self._reasoning = None
        return [self._end(part_id)] if part_id in self._open else []

    def _tool_part(
        self, key: tuple[str, str], view: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        part_id = self._tools.get(key)
        if part_id is not None:
            return part_id, []
        frames = self._close_reasoning()
        view = view or {}
        origin, tool_call_id = key
        fields: dict[str, Any] = {
            "tool_call_id": tool_call_id,
            "tool_name": str(view.get("tool_name") or "tool"),
        }
        if origin != self.run_id:
            fields["origin_run_id"] = origin
        for name in ("skill_name", "classification"):
            if view.get(name):
                fields[name] = view[name]
        part_id, frame = self._start("tool", **fields)
        self._tools[key] = part_id
        frames.append(frame)
        return part_id, frames

    # ── public API ──────────────────────────────────────────────────────

    def start(self, *, role: str) -> str:
        # The security-context version is a private binding (public projections
        # strip it); the client re-reads its session after ``role.changed``.
        self.started = True
        return self._frame(RUN_STARTED, {"protocol_version": PROTOCOL_VERSION, "role": role})

    def translate(self, frame: str) -> list[str]:
        name, payload = parse_frame(frame)
        if not name:
            return []
        body = _body(payload)
        origin = str(payload.get("run_id") or self.run_id)
        if name == events.EVENT_PING:
            return [self._frame(PING, {})]
        if name in PASSTHROUGH:
            return [self._frame(name, body)]
        if name == events.EVENT_THINKING:
            return self._thinking(body)
        if name == events.EVENT_PLAN:
            return self._plan_frames(body)
        if name == events.EVENT_TOOL_CALL:
            return self._tool_call(origin, body)
        if name == events.EVENT_TOOL_STATUS:
            return self._tool_status(
                (origin, str(body.get("tool_call_id") or "")), str(body.get("status") or ""),
            )
        if name == events.EVENT_TOOL_PROGRESS:
            return self._tool_progress(origin, body)
        if name == events.EVENT_TOOL_DETAIL:
            return self._tool_detail(origin, body)
        if name in {"evidence_health", "evidence_envelope"}:
            return self._evidence(origin, name, body)
        if name == "guard":
            frames = self._close_reasoning()
            part_id, opened = self._start("guard", decision=body)
            return [*frames, opened, self._end(part_id)]
        if name == events.EVENT_TEXT_DELTA:
            return self._text(body)
        if name in _CONTENT_TYPES:
            return self._block(_CONTENT_TYPES[name], body)
        if name == "ui":
            return self._ui(body)
        if name in {"ui_delta", events.EVENT_CONTENT_BLOCK_DONE}:
            target = self._content.get(str(body.get("content_id") or ""))
            if target is None or target not in self._open:
                return []
            if name == "ui_delta":
                return [self._delta(target, body.get("text"))]
            return [self._end(target)]
        if name == events.EVENT_INPUT_REQUIRED:
            return self._input_required(origin, body)
        if name == events.EVENT_INPUT_RESOLVED:
            return self._input_resolved(origin, body)
        if name == "role_changed":
            return self._role_changed(body)
        if name == events.EVENT_ERROR:
            return [self._frame(RUN_ERROR, {
                "code": str(body.get("code") or "error"),
                "message": str(body.get("message") or ""),
            })]
        if name == events.EVENT_DONE:
            return self.finish(body)
        return []

    def finish(self, done: dict[str, Any], *, force: bool = False) -> list[str]:
        """Seal every open part, then the terminal ``run.finished``.

        ``force`` re-issues the terminal event when an earlier one was encoded
        but never delivered (its journal write failed or the run was cancelled).
        """
        if self.finished and not force:
            return []
        self.finished = True
        reason = str(done.get("finish_reason") or "stop")
        state = "cancelled" if reason == "cancelled" else (
            "error" if reason in {"error", "interrupted"} else "done"
        )
        self._reasoning = None
        frames: list[str] = []
        waiting = {
            part_id for key, part_id in self._tools.items()
            if self._tool_state.get(key) == "pending"
        }
        for part_id, part_type in list(self._open.items()):
            # The client seals parts left open the same way (``sealState``): a
            # call still awaiting consent never ran. A normally finished run
            # made every block final, an interactive program whose own close
            # frame was lost included; the renderer still validates it.
            if part_type == "tool" and part_id in waiting:
                frames.append(self._end(part_id, "cancelled"))
            elif part_type == "hitl_request":
                # Still waiting when the run ended: nobody can answer it now.
                status = "expired" if reason == "consent_timeout" else "cancelled"
                frames.append(self._frame(INPUT_RESOLVED, {
                    "part_id": part_id, "status": status,
                    "summary": "The request expired" if status == "expired"
                    else "Stopped before you answered",
                }))
                frames.append(self._end(part_id, "cancelled"))
            else:
                frames.append(self._end(part_id, state))
        payload: dict[str, Any] = {
            "message_id": str(done.get("message_id") or ""),
            "finish_reason": reason,
        }
        for key in ("usage", "source"):
            if done.get(key):
                payload[key] = done[key]
        frames.append(self._frame(RUN_FINISHED, payload))
        return frames

    # ── mappings ────────────────────────────────────────────────────────

    def _thinking(self, body: dict[str, Any]) -> list[str]:
        phase = str(body.get("phase") or "act")
        text = str(body.get("text") or "")
        status = str(body.get("status") or "running")
        current = self._reasoning
        if current is not None and current[1] == phase and current[0] in self._open:
            if status == "done":
                self._reasoning = None
                return [self._end(current[0], final=text if text != current[2] else None)]
            if text == current[2]:
                return []
            # The same phase restated: one row whose label follows the loop.
            self._reasoning = (current[0], phase, text)
            return [self._frame(PART_DELTA, {"part_id": current[0], "text": text, "replace": True})]
        frames = self._close_reasoning()
        part_id, opened = self._start("reasoning", source="nova", phase=phase, text=text)
        frames.append(opened)
        if status == "done":
            frames.append(self._end(part_id))
        else:
            self._reasoning = (part_id, phase, text)
        return frames

    def _plan_frames(self, body: dict[str, Any]) -> list[str]:
        raw = body.get("steps")
        steps: list[Any] = raw if isinstance(raw, list) else []
        text = "\n".join(
            f"{index + 1}. {step.get('text', '')}"
            for index, step in enumerate(steps) if isinstance(step, dict)
        )
        if self._plan is not None:
            return [self._frame(PART_DELTA, {"part_id": self._plan, "text": text, "replace": True})]
        frames = self._close_reasoning()
        part_id, opened = self._start(
            "reasoning", source="nova", phase="plan", text=text, plan_steps=len(steps),
        )
        self._plan = part_id
        return [*frames, opened, self._end(part_id)]

    def _tool_call(self, origin: str, view: dict[str, Any]) -> list[str]:
        tool_call_id = str(view.get("tool_call_id") or "")
        if not tool_call_id:
            return []
        key = (origin, tool_call_id)
        part_id, frames = self._tool_part(key, view)
        status = str(view.get("status") or "running")
        preview = view.get("sql_preview") or None
        if status == "pending":
            # The call waits for approval; the request itself follows as a
            # ``hitl_request`` part (``input_required``).
            self._tool_state[key] = status
            payload: dict[str, Any] = {"part_id": part_id, "status": status}
            if preview:
                payload["preview"] = preview
            frames.append(self._frame(TOOL_STATUS, payload))
            return frames
        frames.extend(self._tool_status(key, status, preview=preview))
        return frames

    def _tool_status(self, key: tuple[str, str], status: str, *, preview: Any = None) -> list[str]:
        if not key[1] or not status:
            return []
        part_id, frames = self._tool_part(key)
        if part_id not in self._open:
            return frames
        self._tool_state[key] = status
        payload: dict[str, Any] = {"part_id": part_id, "status": status}
        if preview:
            payload["preview"] = preview
        frames.append(self._frame(TOOL_STATUS, payload))
        if status in _TERMINAL_TOOL:
            frames.append(self._end(part_id, "error" if status == "failed" else (
                "cancelled" if status in {"denied", "cancelled"} else "done"
            )))
        return frames

    def _tool_progress(self, origin: str, body: dict[str, Any]) -> list[str]:
        tool_call_id = str(body.get("tool_call_id") or "")
        if not tool_call_id:
            return []
        part_id, frames = self._tool_part((origin, tool_call_id))
        if part_id not in self._open:
            return frames
        stage = str(body.get("stage") or "")
        status = "done" if stage == "query_completed" else "running"
        payload: dict[str, Any] = {
            "part_id": part_id,
            "status": status,
            "progress": {"stage": stage, "text": str(body.get("text") or "")},
        }
        if body.get("sql_preview"):
            payload["preview"] = body["sql_preview"]
        frames.append(self._frame(TOOL_STATUS, payload))
        return frames

    def _tool_detail(self, origin: str, body: dict[str, Any]) -> list[str]:
        key = (origin, str(body.get("tool_call_id") or ""))
        part_id = self._tools.get(key)
        if part_id is None:
            return []
        # A detail can follow the call's terminal status; it still annotates it.
        extra = {k: v for k, v in body.items() if k not in {"tool_call_id", "text"}}
        payload: dict[str, Any] = {
            "part_id": part_id,
            "status": self._tool_state.get(key, "done"),
            "detail": str(body.get("text") or ""),
        }
        if extra:
            payload["evidence"] = extra
        return [self._frame(TOOL_STATUS, payload)]

    def _evidence(self, origin: str, name: str, body: dict[str, Any]) -> list[str]:
        tool_call_id = str(body.get("tool_call_id") or "")
        part_id = self._tools.get((origin, tool_call_id))
        if part_id is None:
            return []
        payload = {"part_id": part_id, "kind": name.removeprefix("evidence_"),
                   **{k: v for k, v in body.items() if k != "tool_call_id"}}
        payload["tool_call_id"] = tool_call_id
        return [self._frame(TOOL_EVIDENCE, payload)]

    def _text(self, body: dict[str, Any]) -> list[str]:
        content_id = str(body.get("content_id") or "text")
        part_id = self._content.get(content_id)
        frames: list[str] = []
        if part_id is None or part_id not in self._open:
            frames.extend(self._close_reasoning())
            fields = _position(body)
            part_id, opened = self._start("text", **fields)
            self._content[content_id] = part_id
            frames.append(opened)
        frames.append(self._delta(part_id, body.get("text")))
        return frames

    def _block(self, part_type: str, body: dict[str, Any]) -> list[str]:
        content_id = str(body.get("content_id") or "")
        frames = self._close_reasoning()
        fields = _position(body)
        block = body.get("payload") if part_type == "dashboard" else {
            k: v for k, v in body.items() if k not in {"content_index", "content_id"}
        }
        part_id, opened = self._start(part_type, block=block, **fields)
        frames.append(opened)
        if content_id:
            self._content[content_id] = part_id
        # A block arrives whole; the following ``content_block_done`` seals it.
        if not content_id:
            frames.append(self._end(part_id))
        return frames

    def _ui(self, body: dict[str, Any]) -> list[str]:
        content_id = str(body.get("content_id") or "")
        if not content_id or content_id in self._content:
            return []
        frames = self._close_reasoning()
        fields = {"block": body.get("payload") or {}, **_position(body)}
        part_id, opened = self._start("ui", **fields)
        self._content[content_id] = part_id
        frames.append(opened)
        return frames

    def _input_required(self, origin: str, body: dict[str, Any]) -> list[str]:
        request_id = str(body.get("request_id") or "")
        request = body.get("request")
        if not request_id or not isinstance(request, dict) or (origin, request_id) in self._inputs:
            return []
        frames = self._close_reasoning()
        fields: dict[str, Any] = {"request_id": request_id, "kind": str(request.get("kind") or "")}
        tool_call_id = body.get("tool_call_id")
        if tool_call_id:
            tool_part = self._tools.get((origin, str(tool_call_id)))
            if tool_part is not None:
                fields["tool_part_id"] = tool_part
        part_id, opened = self._start("hitl_request", **fields)
        self._inputs[(origin, request_id)] = part_id
        frames.append(opened)
        frames.append(self._frame(INPUT_REQUIRED, {
            "part_id": part_id,
            "request_id": request_id,
            "kind": fields["kind"],
            "request": request,
            "expires_at": str(body.get("expires_at") or ""),
        }))
        return frames

    def _input_resolved(self, origin: str, body: dict[str, Any]) -> list[str]:
        part_id = self._inputs.get((origin, str(body.get("request_id") or "")))
        if part_id is None or part_id not in self._open:
            return []
        status = str(body.get("status") or "cancelled")
        payload: dict[str, Any] = {
            "part_id": part_id, "status": status, "summary": str(body.get("summary") or ""),
        }
        if isinstance(body.get("answer"), dict):
            payload["answer"] = body["answer"]
        return [
            self._frame(INPUT_RESOLVED, payload),
            self._end(part_id, "cancelled" if status in {"cancelled", "expired"} else "done"),
        ]

    def _role_changed(self, body: dict[str, Any]) -> list[str]:
        # Work done under the previous role is discarded by the loop; its parts
        # are closed so nothing from that role keeps streaming.
        frames = [
            self._end(part_id, "cancelled") for part_id in list(self._open)
        ]
        self._reasoning = None
        self._plan = None
        self._tools.clear()
        self._tool_state.clear()
        self._content.clear()
        self._inputs.clear()
        role = str(body.get("active_role") or "")
        frames.append(self._frame(ROLE_CHANGED, {"active_role": role}))
        return frames


def stored_journal(frames: list[str]) -> list[dict[str, Any]]:
    """The delivered v2 frames as the journal kept on the assistant message.

    Only events from the last ``role.changed`` on are kept: earlier ones were
    produced under the previous role and are not shown to the new one.
    """
    entries: list[dict[str, Any]] = []
    for frame in frames:
        name, payload = parse_frame(frame)
        if not name or name in NOT_STORED:
            continue
        if name == ROLE_CHANGED:
            entries = [entry for entry in entries if entry["event"] == RUN_STARTED]
        entries.append({"event": name, "data": payload})
    return entries


def journal_step(frames: list[str]) -> dict[str, Any]:
    return {
        "kind": "event_journal",
        "protocol_version": PROTOCOL_VERSION,
        "events": stored_journal(frames),
    }


def journal_protocol(steps: list[Any]) -> int:
    """``2`` when a stored turn carries its v2 event journal, else ``1``."""
    return PROTOCOL_VERSION if any(
        isinstance(step, dict) and step.get("kind") == "event_journal"
        and step.get("protocol_version") == PROTOCOL_VERSION
        for step in steps
    ) else 1


def without_part_types(step: Any, types: set[str]) -> Any:
    """A stored journal without the parts of ``types`` and their events.

    Interactive blocks stay in the security context they were made in; the
    journal must not carry them past the step filter that already hides them.
    """
    if not (isinstance(step, dict) and step.get("kind") == "event_journal"):
        return step
    hidden: set[str] = set()
    kept: list[Any] = []
    for entry in step.get("events") or []:
        data = entry.get("data") if isinstance(entry, dict) else None
        if not isinstance(data, dict):
            continue
        if entry.get("event") == PART_START and data.get("type") in types:
            hidden.add(str(data.get("part_id")))
            continue
        if str(data.get("part_id")) in hidden:
            continue
        kept.append(entry)
    return {**step, "events": kept}
