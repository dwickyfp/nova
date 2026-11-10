"""Normalised cell outputs.

Kernel events (stdout, stderr, results, errors) and Nova payloads (captured
displays, SQL results) become one ordered list of output records the UI renders
by type. Nothing here interprets text as markup: HTML stays HTML and is shown
by the UI only inside a sandboxed frame; tracebacks lose their terminal colour
codes; every record is bounded and the list as a whole is capped with an
explicit truncation record.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.notebooks.runtime.provider import KernelEvent

PAYLOAD_MIME = "application/vnd.nova.payload+json"
TABLE_MIME = "application/vnd.nova.dataframe+json"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_IMAGE_MIMES = ("image/png", "image/jpeg")
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_TRACEBACK_LINES = 120


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def payload_of(bundle: dict[str, Any]) -> dict[str, Any] | None:
    """The Nova payload in a result bundle, if it carries one."""
    raw = bundle.get(PAYLOAD_MIME)
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _table_of(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
        return None
    return {
        "schema": [
            {"name": str(c.get("name")), "type": str(c.get("type"))}
            for c in raw.get("schema") or []
            if isinstance(c, dict)
        ][:500],
        "rows": raw["rows"][:1000],
        "limit": raw.get("limit"),
        "total_rows": raw.get("total_rows"),
        "truncated": bool(raw.get("truncated")),
        "origin": raw.get("origin") or "python_memory",
    }


def from_bundle(bundle: dict[str, Any]) -> dict[str, Any] | None:
    """One output record from a MIME bundle, richest type first."""
    if TABLE_MIME in bundle:
        table = _table_of(bundle[TABLE_MIME])
        if table is not None:
            return {"type": "table", "table": table}
    for mime in _IMAGE_MIMES:
        data = bundle.get(mime)
        if isinstance(data, str):
            try:
                decoded = base64.b64decode(data, validate=False)
            except (binascii.Error, ValueError):
                continue
            if len(decoded) > _MAX_IMAGE_BYTES:
                return {"type": "text", "text": "Image too large to show"}
            return {"type": "image", "mime": mime, "data": data.replace("\n", "")}
    if isinstance(bundle.get("image/svg+xml"), str):
        return {"type": "svg", "svg": bundle["image/svg+xml"]}
    if isinstance(bundle.get("text/html"), str):
        return {
            "type": "html",
            "html": bundle["text/html"],
            "text": strip_ansi(str(bundle.get("text/plain") or "")),
        }
    if isinstance(bundle.get("text/markdown"), str):
        return {"type": "markdown", "text": bundle["text/markdown"]}
    text = bundle.get("text/plain")
    if isinstance(text, str) and text:
        return {"type": "text", "text": strip_ansi(text)}
    return None


@dataclass
class OutputCollector:
    """Builds the ordered, bounded output list of one execution."""

    max_bytes: int
    records: list[dict[str, Any]] = field(default_factory=list)
    size: int = 0
    truncated: bool = False
    error: dict[str, Any] | None = None
    kernel_execution_id: str = ""
    execution_count: int | None = None

    def _add(self, record: dict[str, Any], *, at: float = 0.0) -> dict[str, Any] | None:
        if self.truncated:
            return None
        encoded = len(json.dumps(record, default=str))
        if self.size + encoded > self.max_bytes:
            self.truncated = True
            notice = {
                "type": "truncated",
                "text": "Output too large; the first part is shown.",
            }
            self.records.append(notice)
            return notice
        self.size += encoded
        record["_t"] = at
        self.records.append(record)
        return record

    def stream(self, name: str, text: str, *, at: float) -> dict[str, Any] | None:
        text = strip_ansi(text)
        if not text:
            return None
        last = self.records[-1] if self.records else None
        if last and last.get("type") == "stream" and last.get("name") == name:
            if self.size + len(text) > self.max_bytes:
                return self._add({"type": "stream", "name": name, "text": text}, at=at)
            last["text"] += text
            self.size += len(text)
            return {"type": "stream", "name": name, "text": text, "_append": True}
        return self._add({"type": "stream", "name": name, "text": text}, at=at)

    def bundle(self, bundle: dict[str, Any], *, at: float) -> dict[str, Any] | None:
        record = from_bundle(bundle)
        return None if record is None else self._add(record, at=at)

    def record(self, record: dict[str, Any], *, at: float = 0.0) -> dict[str, Any] | None:
        return self._add(record, at=at)

    def kernel_error(self, error: dict[str, Any], *, at: float) -> dict[str, Any] | None:
        traceback = [strip_ansi(line) for line in error.get("traceback") or []]
        if len(traceback) > _MAX_TRACEBACK_LINES:
            traceback = traceback[:20] + ["…"] + traceback[-80:]
        record = {
            "type": "error",
            "ename": error.get("name") or "Error",
            "evalue": strip_ansi(str(error.get("value") or "")),
            "traceback": traceback,
        }
        self.error = {"name": record["ename"], "message": record["evalue"]}
        return self._add(record, at=at)

    def nova_error(self, name: str, message: str) -> dict[str, Any] | None:
        self.error = {"name": name, "message": message}
        return self._add({"type": "error", "ename": name, "evalue": message, "traceback": []})

    def displays(self, displays: list[dict[str, Any]]) -> None:
        """Merge captured displays into the list by time."""
        for display in displays:
            data = display.get("data") if isinstance(display, dict) else None
            if not isinstance(data, dict):
                continue
            record = from_bundle(data)
            if record is not None:
                self._add(record, at=float(display.get("t") or 0.0))
        self.records.sort(key=lambda r: (r.get("type") == "truncated", r.get("_t") or 0.0))

    def final(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in r.items() if k != "_t"} for r in self.records]

    def accept(self, event: KernelEvent) -> dict[str, Any] | None:
        """Feed one kernel event of the user's code; return a live record."""
        if event.type == "init":
            self.kernel_execution_id = event.text
            return None
        if event.type in {"stdout", "stderr"}:
            return self.stream(event.type, event.text, at=event.timestamp)
        if event.type == "result":
            if payload_of(event.bundle) is not None:
                return None
            return self.bundle(event.bundle, at=event.timestamp)
        if event.type == "error":
            return self.kernel_error(event.error, at=event.timestamp)
        return None


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """A small description of the outputs, kept with the execution row."""
    kinds: dict[str, int] = {}
    table: dict[str, Any] | None = None
    for record in records:
        kinds[record["type"]] = kinds.get(record["type"], 0) + 1
        if record["type"] == "table" and table is None:
            meta = record["table"]
            table = {
                "columns": [c["name"] for c in meta["schema"]][:50],
                "preview_rows": len(meta["rows"]),
                "total_rows": meta.get("total_rows"),
                "origin": meta.get("origin"),
            }
    return {"kinds": kinds, "table": table}
