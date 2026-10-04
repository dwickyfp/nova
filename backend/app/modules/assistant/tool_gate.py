"""The shared schema and consent boundary for loop and supervised operations."""

import asyncio

from app.modules.assistant.intelligence import validate_json_arguments
from app.modules.assistant.tools import requires_consent


def validate_tool_invocation(tool, invocation, schema=None):
    return validate_json_arguments(schema or tool.parameters, invocation.arguments)


def needs_tool_consent(tool, classification, thread) -> bool:
    return requires_consent(tool) and not thread.consent.covers(classification)


async def resolve_tool_consent(
    tool,
    invocation,
    classification,
    thread,
    resolve_consent,
    *,
    timeout_seconds=300,
):
    if not needs_tool_consent(tool, classification, thread):
        return True
    return await asyncio.wait_for(
        resolve_consent(invocation, classification),
        timeout=timeout_seconds,
    )
