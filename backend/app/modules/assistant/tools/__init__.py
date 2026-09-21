"""Assistant tools package.

This package **is** the Stage B tool boundary (the former ``tools.py``) and the
home of the Stage C tools. Keeping the boundary definitions here means the
import path every module already uses — ``app.modules.assistant.tools`` — keeps
working unchanged while ``query_execute`` lives at the spec's path
``app.modules.assistant.tools.query_execute``.

Stage B defines the boundary the loop uses; Stage C registers ``query_execute``
against it in ``app.modules.assistant.registry``. Keeping the registry separate
is what lets T-C1 land without touching the loop's control flow, and what keeps
the loop testable with a fake tool.
"""

from __future__ import annotations

from app.modules.assistant.tools._boundary import (
    AssistantTool,
    ConsentResolver,
    ToolInvocation,
    ToolOutcome,
    ToolRegistry,
    record_provider_usage,
    report_tool_progress,
    requires_consent,
)

__all__ = [
    "AssistantTool",
    "ConsentResolver",
    "ToolInvocation",
    "ToolOutcome",
    "ToolRegistry",
    "record_provider_usage",
    "report_tool_progress",
    "requires_consent",
]
