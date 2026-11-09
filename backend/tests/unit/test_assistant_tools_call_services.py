"""Assistant and agent tools call Nova's services, never Nova's own HTTP routes.

Tools run in-process as the caller. Going through Nova's HTTP surface would
put route names in tool code, prompts and answers, and would act outside the
caller's governed session. Only modules that talk to services outside Nova
(the AI provider, MCP servers, web search, open media) may hold an HTTP client.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app" / "modules"
SCANNED = [APP / "assistant", APP / "agents"]

#: Modules allowed an HTTP client, each for a service outside Nova.
EXTERNAL_HTTP = {
    "assistant/provider.py",  # the configured AI provider
    "assistant/measurements.py",  # counts provider dispatches
    "assistant/ui_media.py",  # public images for answers
    "agents/mcp_client.py",  # MCP servers an agent owner configured
}

#: Files that name another service's routes (never Nova's), never shown to a model.
OTHER_SERVICE_ROUTES = {
    "agents/dataviz_assistant.py",  # Superset's dashboard datasets, through its BI client
}

HTTP_CLIENT = re.compile(r"^\s*(?:import httpx|from httpx|import requests|import aiohttp)", re.M)
SELF_ROUTE = re.compile(r"""["'](?:https?://[^"']*)?/api/v1/""")


def _sources():
    for root in SCANNED:
        for path in root.rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path.relative_to(APP).as_posix(), path.read_text()


def test_only_external_services_get_an_http_client():
    offenders = [name for name, text in _sources() if HTTP_CLIENT.search(text)]
    assert sorted(set(offenders) - EXTERNAL_HTTP) == []


def test_no_tool_or_prompt_names_a_nova_route():
    offenders = [name for name, text in _sources() if SELF_ROUTE.search(text)]
    assert sorted(set(offenders) - OTHER_SERVICE_ROUTES) == []
