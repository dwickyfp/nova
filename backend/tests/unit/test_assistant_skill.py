"""Default Nova SQL skill (T-E1) — assembler, budget, and prompt safety.

Unit tests only: the assembler reads ``docs/sql_docs/`` from the repo, so these
assert against the real corpus and the real seed prompt. No network, no
StarRocks, no provider.

Coverage maps to the acceptance criteria the skill work can verify without Stage
C's tool execution (issue NOVA-78):

* AC #4 — no provider request built with the skill carries a credential shape;
* AC #5 — assembly is deterministic (same inputs → byte-identical prompt);
* AC #6 — the token budget is enforced and the failure names the section;
* AC #7 (assembly portion) — the existing loop keeps the seed verbatim and the
  default injection does not change loop control flow.
"""

from __future__ import annotations

from app.modules.assistant.service import (
    _DEFAULT_SYSTEM_PROMPT,
    AssistantLoop,
    _default_skill_prompt,
)
from app.modules.assistant.skills import (
    DEFAULT_SKILL_PROMPT,
    EXCERPT_CLOSE,
    EXCERPT_OPEN,
    NovaSqlSkill,
    SkillError,
    contains_credential_shape,
    default_skill,
)
from app.modules.assistant.tools import ToolRegistry

# ── AC #4: no credential shape in a skill-built request ──────────────────────


def test_assembled_prompt_carries_no_credential_shape():
    """The default prompt is admitted only if it is credential-free."""
    assert not contains_credential_shape(DEFAULT_SKILL_PROMPT)


def test_credential_shape_detector_accepts_placeholders_rejects_values():
    assert not contains_credential_shape("'aws.s3.access_key'='***'")
    assert not contains_credential_shape("'aws.s3.secret_key'='K'")
    assert not contains_credential_shape("access_key=<value>")
    assert contains_credential_shape("'aws.s3.access_key'='AKIAIOSFODNN7EXAMPLE'")
    assert contains_credential_shape('aws.s3.secret_key="s3cr3t-value"')
    assert contains_credential_shape("gcp.gcs.service_account_private_key='abc123'")


def test_provider_request_from_the_loop_carries_no_credential_shape():
    """AC #4, at the request boundary: the loop's system message is clean."""
    loop = AssistantLoop(provider=_NullProvider(), registry=ToolRegistry())
    assert not contains_credential_shape(loop._system_prompt)


def test_user_message_with_a_credential_is_not_the_skill_s_doing():
    """The skill itself never injects a credential; a user still can type one.

    This pins the boundary: the assembler's output is clean regardless, and the
    detector is what would catch a regression in a source doc.
    """
    for section in default_skill.sections:
        assert not contains_credential_shape(section.text), section.name


# ── AC #5: deterministic assembly ────────────────────────────────────────────


def test_same_inputs_produce_byte_identical_prompt():
    first = NovaSqlSkill()
    second = NovaSqlSkill()
    assert first.build_system_prompt() == second.build_system_prompt()
    assert first.metadata.revision == second.metadata.revision


def test_prompt_is_cached_and_stable_across_calls():
    skill = NovaSqlSkill()
    assert skill.build_system_prompt() is skill.build_system_prompt()


def test_revision_changes_when_a_source_doc_changes(monkeypatch, tmp_path):
    """The provenance hash is a real content hash, not a constant."""
    original = NovaSqlSkill().metadata.revision
    import app.modules.assistant.skills as skills_module

    docs = tmp_path / "docs" / "sql_docs"
    docs.mkdir(parents=True)
    for name in skills_module._SOURCE_DOCS:
        (docs / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(skills_module, "_SQL_DOCS_DIR", docs)
    assert skills_module._source_revision() != original


# ── AC #6: token budget is enforced, failure names the section ───────────────


def test_default_primer_is_within_budget():
    assert default_skill.metadata.tokens <= default_skill.metadata.token_budget
    assert default_skill.metadata.token_budget == 2000


def test_budget_violation_names_the_offending_section():
    with __import__("pytest").raises(SkillError) as excinfo:
        NovaSqlSkill(token_budget=10)
    message = str(excinfo.value)
    assert "exceeds" in message
    assert "identity" in message
    assert "tokens" in message


def test_metadata_reports_revision_budget_and_document_count():
    metadata = default_skill.metadata
    assert len(metadata.revision) == 64
    assert metadata.document_count == 5
    assert metadata.sections[0] == "identity"
    assert "refusal-rules" in metadata.sections


# ── AC #7 (assembly portion): the loop keeps its contract ────────────────────


def test_default_injection_uses_the_assembled_skill():
    loop = AssistantLoop(provider=_NullProvider(), registry=ToolRegistry())
    assert loop._system_prompt == DEFAULT_SKILL_PROMPT
    assert loop._system_prompt == _default_skill_prompt()


def test_explicit_system_prompt_still_wins():
    loop = AssistantLoop(provider=_NullProvider(), registry=ToolRegistry(), system_prompt="custom")
    assert loop._system_prompt == "custom"


def test_seed_prompt_is_preserved_verbatim_and_first():
    prompt = DEFAULT_SKILL_PROMPT
    assert prompt.startswith(_DEFAULT_SYSTEM_PROMPT.strip())
    # The seed is not paraphrased into the excerpt blocks.
    assert not prompt.startswith(EXCERPT_OPEN)


def test_excerpts_are_delimited_as_data():
    prompt = DEFAULT_SKILL_PROMPT
    assert prompt.count(EXCERPT_OPEN) == len(default_skill.sections)
    assert prompt.count(EXCERPT_CLOSE) == len(default_skill.sections)
    # The seed rules stay outside every data block, so they remain instructions.
    seed = _DEFAULT_SYSTEM_PROMPT.strip()
    assert prompt.index(seed) < prompt.index(EXCERPT_OPEN)


def test_retrieve_returns_one_delimited_section():
    excerpt = default_skill.retrieve("stage-queries")
    assert excerpt.startswith(EXCERPT_OPEN)
    assert excerpt.endswith(EXCERPT_CLOSE)
    assert "@stage" in excerpt


def test_retrieve_unknown_section_raises():
    import pytest

    with pytest.raises(KeyError):
        default_skill.retrieve("no-such-section")


def test_retrieve_document_reads_on_demand():
    excerpt = default_skill.retrieve_document("11-query-catalog.md")
    assert excerpt.startswith(EXCERPT_OPEN)
    # The full catalog is longer than the distilled section.
    assert len(excerpt) > len(default_skill.retrieve("statement-catalog"))


def test_retrieve_document_rejects_an_unlisted_file():
    import pytest

    with pytest.raises(KeyError):
        default_skill.retrieve_document("../../etc/passwd")


# ── helpers ───────────────────────────────────────────────────────────────────


class _NullProvider:
    async def resolve(self):  # pragma: no cover - never awaited in these tests
        raise AssertionError("the provider must not be called while building a prompt")

    async def complete(self, **kwargs):  # pragma: no cover
        raise AssertionError("the provider must not be called while building a prompt")
