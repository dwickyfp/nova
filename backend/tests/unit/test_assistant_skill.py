"""Default Nova SQL skill (T-E1) — assembler, budget, and prompt safety.

Unit tests only: the assembler reads ``docs/sql_docs/`` from the repo, so these
assert against the real corpus and the real seed prompt. No network, no
StarRocks, no provider.

Coverage maps to the acceptance criteria the skill work can verify without Stage
C's tool execution (issue NOVA-78):

* AC #4 — no provider request built with the skill carries a credential shape;
* AC #5 — assembly is deterministic (same inputs → byte-identical prompt);
* AC #6 — the token budget is enforced (seed included) and the failure names
  the section;
* AC #7 (assembly portion) — the existing loop keeps the seed verbatim and the
  default injection does not change loop control flow.

Plus regressions from QA on PR #83: every `_SOURCE_DOCS` doc must be retrievable
(Finding 1) and the enforced budget must count the seed prompt (Finding 2).
"""

from __future__ import annotations

from app.modules.assistant.registry import build_registry
from app.modules.assistant.service import _DEFAULT_SYSTEM_PROMPT, AssistantLoop, LoopContext
from app.modules.assistant.skill_registry import skill_library
from app.modules.assistant.skills import (
    _SOURCE_DOCS,
    DEFAULT_SKILL_PROMPT,
    EXCERPT_CLOSE,
    EXCERPT_OPEN,
    NovaSqlSkill,
    SkillError,
    _estimate_tokens,
    contains_credential_shape,
    default_skill,
)
from app.modules.assistant.state import AssistantThread
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
    # Raised from 2000 to fit the scope-boundary guardrail section, then to 3000
    # to fit the writing-style rules in the seed.
    assert default_skill.metadata.token_budget == 3000


def test_budget_counts_the_seed_prompt():
    """The enforced/reported number is the whole skill, seed included.

    Regression for QA Finding 2: the budget previously covered only the primer,
    so a future seed expansion could blow the budget while the metadata and the
    `tokens <= token_budget` assertion stayed green.
    """
    seed_tokens = _estimate_tokens(_DEFAULT_SYSTEM_PROMPT.strip())
    primer_tokens = sum(section.tokens for section in default_skill.sections)
    assert default_skill.metadata.tokens == seed_tokens + primer_tokens
    assert default_skill.metadata.tokens > primer_tokens


def test_budget_violation_names_the_seed_and_a_section():
    with __import__("pytest").raises(SkillError) as excinfo:
        NovaSqlSkill(token_budget=10)
    message = str(excinfo.value)
    assert "exceeds" in message
    assert "seed" in message
    assert "identity" in message
    assert "tokens" in message


def test_metadata_reports_revision_budget_and_document_count():
    metadata = default_skill.metadata
    assert len(metadata.revision) == 64
    assert metadata.document_count == 5
    assert metadata.sections[0] == "identity"
    assert "scope-boundary" in metadata.sections
    assert "refusal-rules" in metadata.sections


# ── AC #7 (assembly portion): the loop keeps its contract ────────────────────


def test_default_injection_uses_the_assembled_skill_without_global_catalog():
    loop = AssistantLoop(provider=_NullProvider(), registry=ToolRegistry())
    # The assembled default procedure is the base prompt. Discoverable skills
    # are selected per turn; the global catalog is never dumped into context.
    assert loop._system_prompt.startswith(DEFAULT_SKILL_PROMPT)
    assert "Available skills" not in loop._system_prompt
    assert "`create-table`" not in loop._system_prompt


def test_explicit_system_prompt_still_wins_as_the_base():
    loop = AssistantLoop(provider=_NullProvider(), registry=ToolRegistry(), system_prompt="custom")
    assert loop._system_prompt.startswith("custom")


def test_seed_prompt_is_preserved_verbatim_and_first():
    prompt = DEFAULT_SKILL_PROMPT
    assert prompt.startswith(_DEFAULT_SYSTEM_PROMPT.strip())
    # The seed is not paraphrased into the excerpt blocks.
    assert not prompt.startswith(EXCERPT_OPEN)


def test_seed_prompt_names_the_assistant_nove():
    assert "Nove" in _DEFAULT_SYSTEM_PROMPT


def test_seed_prompt_permits_authoring_account_ddl():
    """Regression: a benign "write me a CREATE USER" must not be refused.

    The model over-refused after being told the read-only tool denies CREATE; the
    seed prompt must state that authoring SQL (including account/role DDL) is
    allowed, while only the four protected-object operations are forbidden.
    """
    prompt = _DEFAULT_SYSTEM_PROMPT
    assert "Authoring SQL text is always allowed" in prompt
    assert "CREATE USER" in prompt
    # The read-only restriction is scoped to the tool, not to authoring.
    assert "query_execute" in prompt
    # The four protected-object operations stay forbidden.
    assert "DROP ROLE ACCOUNTADMIN" in prompt


def test_seed_prompt_bounds_the_assistant_to_nova_scope():
    """Off-topic questions (e.g. "siapa jokowi?") must be declined, not answered.

    The boundary is a standing instruction, not advisory: the seed must scope
    the assistant to the Nova warehouse, name the out-of-scope classes, and say
    the boundary is not bypassable.
    """
    prompt = _DEFAULT_SYSTEM_PROMPT
    assert "Nova data-warehouse work only" in prompt
    assert "not bypassable" in prompt
    # The classic jailbreak framings are named so the model recognises them.
    assert "pretend" in prompt
    assert "persona" in prompt
    # The decline is short and pre-emptive, not a partial answer.
    assert "one short sentence" in prompt


def test_primer_carries_the_scope_boundary_section():
    """The boundary ships in the assembled skill too, not only the seed."""
    boundary = default_skill.retrieve("scope-boundary")
    assert "Decline anything else" in boundary
    assert "not bypassable" in boundary


def test_seed_prompt_carries_the_writing_style_rules():
    """Nove's prose must not read as AI slop.

    Every answer is prose, so the anti-slop rules live in the seed (shipped with
    every turn), not only in the load-on-demand library. This pins the parts
    that matter: the em-dash ban, the chatbot openers/closers, the empty hype
    words, and the no-fabrication rule.
    """
    prompt = _DEFAULT_SYSTEM_PROMPT
    assert "Writing style" in prompt
    assert "No em dashes" in prompt
    assert "Let's dive in" in prompt
    assert "I hope this helps" in prompt
    # Empty hype vocabulary is named so the model can recognise and avoid it.
    assert "seamless" in prompt
    assert "empower" in prompt
    # A fabricated result is a defect, not just a style miss.
    assert "never invent a number" in prompt


def test_writing_style_skill_is_loadable():
    """The long-form writing playbook is browsable and loadable."""
    skill = skill_library.get("writing-style")
    assert skill is not None
    assert "em dash" in skill.body.lower()
    assert skill.triggers
    excerpt = skill_library.load("writing-style")
    assert excerpt.startswith("[nova-skill")
    assert excerpt.rstrip().endswith("[end nova-skill]")


def test_native_ml_skill_is_packaged_and_covers_the_runtime_contract():
    skill = skill_library.get("native-ml")
    assert skill is not None
    assert not contains_credential_shape(skill.body)
    for contract_term in (
        "classification",
        "regression",
        "forecast",
        "anomaly_detection",
        "clustering",
        "ml_execute",
        "ML_PREDICT",
        "ML_FORECAST",
        "persist=false",
        "persist=true",
    ):
        assert contract_term in skill.body


def test_nove_selects_native_ml_without_a_global_skill_catalog():
    registry = build_registry()
    assert set(registry.discoverable_skills) == set(skill_library.names())
    assert registry.skill_definitions["native-ml"].trust_level == "platform_skill"

    loop = AssistantLoop(provider=_NullProvider(), registry=registry)
    context = LoopContext(user_name="alice")
    messages = loop._build_messages(
        AssistantThread(thread_id="t", user_name="alice", title="t"),
        "Forecast revenue for the next 30 days",
        context,
    )

    assert context.selected_skills == ["native-ml"]
    assert '<PLATFORM_SKILL name="native-ml">' in messages[1]["content"]
    assert "Available skills" not in messages[0]["content"]


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


def test_retrieve_document_succeeds_for_every_source_doc():
    """Every doc the skill lists as a source must be retrievable.

    Regression for QA Finding 1: `09-guardrails-invariants.md` was in
    `_SOURCE_DOCS` but its redaction worked-example matched the credential-shape
    screen, so `retrieve_document` raised `SkillError` for it permanently. The
    previous test only exercised `11`, which is why it escaped.
    """
    for name in _SOURCE_DOCS:
        excerpt = default_skill.retrieve_document(name)
        assert excerpt.startswith(EXCERPT_OPEN), name
        assert excerpt.endswith(EXCERPT_CLOSE), name


def test_guardrails_doc_is_credential_free_by_construction():
    """The guide §4.3 invariant: no source doc carries a populated credential."""
    for name in _SOURCE_DOCS:
        assert not contains_credential_shape(_read_sql_doc(name)), name


def test_angle_bracket_placeholders_are_not_credentials():
    """The corpus marks every non-secret value with ``<…>``."""
    from app.modules.assistant.skills import contains_credential_shape

    assert not contains_credential_shape('"aws.s3.access_key" = "<placeholder>"')
    assert not contains_credential_shape("aws.s3.secret_key=<value>")
    assert contains_credential_shape('"aws.s3.access_key" = "AKIAIOSFODNN7EXAMPLE"')


def test_retrieve_document_rejects_an_unlisted_file():
    import pytest

    with pytest.raises(KeyError):
        default_skill.retrieve_document("../../etc/passwd")


# ── helpers ───────────────────────────────────────────────────────────────────


def _read_sql_doc(filename: str) -> str:
    from app.modules.assistant import skills as skills_module

    return (skills_module._SQL_DOCS_DIR / filename).read_text(encoding="utf-8")


class _NullProvider:
    async def resolve(self):  # pragma: no cover - never awaited in these tests
        raise AssertionError("the provider must not be called while building a prompt")

    async def complete(self, **kwargs):  # pragma: no cover
        raise AssertionError("the provider must not be called while building a prompt")

    async def stream(self, **kwargs):  # pragma: no cover
        raise AssertionError("the provider must not be called while building a prompt")
        yield  # pragma: no cover - marks this as an async generator
