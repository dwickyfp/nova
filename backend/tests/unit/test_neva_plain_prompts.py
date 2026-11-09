"""What Neva reads never teaches it to talk about Nova's insides.

The persona, skills, knowledge guides and tool descriptions are the model's
vocabulary. If they mention endpoints, APIs, routes, status codes or code
paths, answers start repeating them. Tool names may appear (the model needs
them to choose a capability); the answer filter turns them into plain words.
"""

import re
from pathlib import Path

import pytest

from app.modules.assistant.plain_language import find_technical_leaks
from app.modules.assistant.registry import tool_registry
from app.modules.assistant.service import _DEFAULT_SYSTEM_PROMPT
from app.modules.assistant.skill_registry import skill_library

ASSISTANT = Path(__file__).resolve().parents[2] / "app" / "modules" / "assistant"
KNOWLEDGE = sorted((ASSISTANT / "knowledge_library").glob("*.md"))
#: A guide that teaches real SQL property names, inside code, by necessity.
REAL_PROPERTY_NAMES = {"ai-functions.md"}
#: What may appear: tool names are the model's own vocabulary.
ALLOWED_KINDS = {"tool_name"}


def _leaks(text: str) -> list[str]:
    return [f"{leak.kind}:{leak.text}" for leak in find_technical_leaks(text)
            if leak.kind not in ALLOWED_KINDS]


def test_the_persona_is_neva_and_plain():
    assert "You are Neva" in _DEFAULT_SYSTEM_PROMPT
    assert not re.search(r"\bNove\b", _DEFAULT_SYSTEM_PROMPT)
    assert _leaks(_DEFAULT_SYSTEM_PROMPT) == []


@pytest.mark.parametrize("guide", KNOWLEDGE, ids=lambda path: path.name)
def test_knowledge_guides_carry_no_internals(guide):
    text = guide.read_text()
    assert not re.search(r"\bNove\b", text)
    assert _leaks(text) == []


@pytest.mark.parametrize("name", skill_library.names())
def test_skill_bodies_carry_no_internals(name):
    # The body is what the model reads; the front matter is catalog metadata.
    body = skill_library.get(name).body
    assert not re.search(r"\bNove\b", body)
    if f"{name}.md" in REAL_PROPERTY_NAMES:
        return
    assert _leaks(body) == []


def test_tool_descriptions_carry_no_internals():
    for name, tool in tool_registry._tools.items():  # noqa: SLF001 - the inventory
        assert _leaks(tool.description) == [], name
