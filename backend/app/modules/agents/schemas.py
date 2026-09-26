"""Agent Studio API schemas — request/response contracts.

Field names mirror the columns in ``repository.py`` so the service layer is a
straight pass-through. The contracts are frozen in
``docs/specs/nova-12-agent-studio-implementation-plan.md`` §4.

Nothing here can carry a credential: an agent is configuration (instructions,
tool names, skill names, a semantic-model id), a semantic model is parsed Ossie
metadata, and a skill is a Markdown playbook. All three are screened before
storage. Agents bind published Semantic Views by ID; legacy model ID fields are
read only after migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.agents.resources import ResourceBindings

#: Which tools an agent bundles. A tool not in this set is never registered for
#: the agent, so the model cannot call it.
AgentToolName = str

#: HITL policy. ``auto_read_only`` runs read-only tools without a prompt;
#: ``ask_every_tool`` prompts for each. There is deliberately no bypass value in
#: v1 (Phase 12 §8).
AgentPolicy = Literal["auto_read_only", "ask_every_tool"]

Visibility = Literal["private", "shared"]


class AgentView(BaseModel):
    agent_id: str
    owner_name: str
    database_name: str | None = None
    schema_name: str | None = None
    name: str
    description: str = ""
    avatar: str | None = None
    color: str | None = None
    model_provider_id: str | None = None
    model_name: str | None = None
    instructions_response: str = ""
    instructions_orchestration: str = ""
    response_style: str | None = None
    sample_questions: list[str] = Field(default_factory=list)
    budget_seconds: int | None = None
    budget_tokens: int | None = None
    tool_not_accessible: str = "accept"
    default_tools: list[str] = Field(default_factory=list)
    default_skills: list[str] = Field(default_factory=list)
    discoverable_skills: list[str] = Field(default_factory=list)
    compiled_instructions: dict = Field(default_factory=dict)
    harness_mode: Literal["auto", "fast", "guided", "strict"] = "auto"
    policy: AgentPolicy = "auto_read_only"
    semantic_model_id: str | None = None
    semantic_model_ids: list[str] = Field(default_factory=list)
    semantic_view_ids: list[str] = Field(default_factory=list)
    visibility: Visibility = "private"
    created_at: datetime
    updated_at: datetime
    resource_bindings: ResourceBindings = Field(default_factory=ResourceBindings)
    config_revision: str | None = None


class AgentListResponse(BaseModel):
    agents: list[AgentView]
    count: int


class AgentCreateRequest(BaseModel):
    resource_bindings: ResourceBindings = Field(default_factory=ResourceBindings)
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    database_name: str | None = None
    schema_name: str | None = None
    avatar: str | None = None
    color: str | None = None
    model_provider_id: str | None = None
    model_name: str | None = None
    instructions_response: str = ""
    instructions_orchestration: str = ""
    response_style: str | None = None
    sample_questions: list[str] = Field(default_factory=list)
    budget_seconds: int | None = Field(default=None, ge=1, le=3600)
    budget_tokens: int | None = Field(default=None, ge=1)
    tool_not_accessible: Literal["accept", "reject"] = "accept"
    default_tools: list[AgentToolName] = Field(default_factory=list)
    default_skills: list[str] = Field(default_factory=list)
    discoverable_skills: list[str] = Field(default_factory=list)
    harness_mode: Literal["auto", "fast", "guided", "strict"] = "auto"
    policy: AgentPolicy = "auto_read_only"
    semantic_model_id: str | None = None
    semantic_model_ids: list[str] = Field(default_factory=list)
    semantic_view_ids: list[str] = Field(default_factory=list)
    visibility: Visibility = "private"


class AgentUpdateRequest(BaseModel):
    """Every field optional — a PATCH semantics update."""

    resource_bindings: ResourceBindings | None = None

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    database_name: str | None = None
    schema_name: str | None = None
    avatar: str | None = None
    color: str | None = None
    model_provider_id: str | None = None
    model_name: str | None = None
    instructions_response: str | None = None
    instructions_orchestration: str | None = None
    response_style: str | None = None
    sample_questions: list[str] | None = None
    budget_seconds: int | None = Field(default=None, ge=1, le=3600)
    budget_tokens: int | None = Field(default=None, ge=1)
    tool_not_accessible: Literal["accept", "reject"] | None = None
    default_tools: list[AgentToolName] | None = None
    default_skills: list[str] | None = None
    discoverable_skills: list[str] | None = None
    harness_mode: Literal["auto", "fast", "guided", "strict"] | None = None
    policy: AgentPolicy | None = None
    semantic_model_id: str | None = None
    semantic_model_ids: list[str] | None = None
    semantic_view_ids: list[str] | None = None
    visibility: Visibility | None = None


class SemanticModelView(BaseModel):
    semantic_model_id: str
    owner_name: str
    name: str
    description: str = ""
    database_name: str | None = None
    schema_name: str | None = None
    ossie_version: str
    definition: dict = Field(default_factory=dict)
    source_file_id: str | None = None
    created_at: datetime
    updated_at: datetime


class SemanticModelListResponse(BaseModel):
    models: list[SemanticModelView]
    count: int


class SemanticModelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    database_name: str | None = None
    schema_name: str | None = None
    #: The Ossie document (YAML or JSON) as text. Parsed and version-pinned by
    #: the semantic parser before it is stored.
    definition: str
    source_file_id: str | None = None


class SemanticValidateRequest(BaseModel):
    definition: str


class SemanticValidateResponse(BaseModel):
    valid: bool
    ossie_version: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    #: Summary counts so the builder can render a preview without a second call.
    dataset_count: int = 0
    metric_count: int = 0
    relationship_count: int = 0


class SemanticPreviewRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class SemanticPreviewResponse(BaseModel):
    semantic_model_id: str
    model_fingerprint: str
    semantic_plan: dict
    generated_sql: str
    confidence: dict
    relationship_path: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SemanticLintView(BaseModel):
    code: str
    severity: str
    message: str
    object_name: str | None = None


class SemanticLintResponse(BaseModel):
    semantic_model_id: str
    model_fingerprint: str
    valid: bool
    errors: list[str] = Field(default_factory=list)
    findings: list[SemanticLintView] = Field(default_factory=list)
    quality: dict = Field(default_factory=dict)


class VerifiedQueryCreateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    semantic_plan: dict
    verified_sql: str = Field(min_length=1, max_length=100_000)
    expected_result_signature: str | None = Field(default=None, max_length=256)
    tags: list[str] = Field(default_factory=list)


class VerifiedQueryView(BaseModel):
    verified_query_id: str
    semantic_model_id: str
    model_fingerprint: str
    question: str
    semantic_plan: dict
    verified_sql: str
    expected_result_signature: str | None = None
    verified_by: str
    verified_at: datetime
    tags: list[str] = Field(default_factory=list)
    usage_count: int = 0
    success_count: int = 0


class VerifiedQueryListResponse(BaseModel):
    queries: list[VerifiedQueryView]
    count: int


class SemanticQualityCase(BaseModel):
    verified_query_id: str
    question: str
    status: Literal["matched", "changed"]


class SemanticQualityLabResponse(BaseModel):
    semantic_model_id: str
    model_fingerprint: str
    total: int
    matched: int
    changed: int
    cases: list[SemanticQualityCase]


class RuleProposalCreateRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    memory_id: str = Field(min_length=1, max_length=64)
    metric_name: str = Field(min_length=1, max_length=160)
    proposed_expression: str = Field(min_length=1, max_length=4000)


class RuleProposalView(BaseModel):
    proposal_id: str
    owner_name: str
    agent_id: str
    role_name: str
    memory_id: str
    semantic_model_id: str
    metric_name: str
    prior_expression: str
    proposed_expression: str
    prior_fingerprint: str
    proposed_fingerprint: str
    status: Literal["pending", "approved", "rejected"]
    previewed_at: datetime | None = None
    reviewed_by: str | None = None
    created_at: datetime
    updated_at: datetime


class RuleProposalListResponse(BaseModel):
    proposals: list[RuleProposalView]
    count: int


class RuleProposalPreviewResponse(BaseModel):
    proposal_id: str
    prior_sql: str
    proposed_sql: str
    prior_value: str | None
    proposed_value: str | None
    metric_name: str
    model_fingerprint: str


class SkillView(BaseModel):
    skill_id: str
    owner_name: str
    name: str
    description: str = ""
    body: str = ""
    scope: str = "user"
    source: Literal["builtin", "user"] = "user"
    read_only: bool = False
    created_at: datetime
    updated_at: datetime


class SkillListResponse(BaseModel):
    skills: list[SkillView]
    count: int


class SkillCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    description: str = Field(default="", max_length=1024)
    body: str = Field(min_length=1, max_length=25 * 1024 * 1024)
    scope: Literal["user", "global"] = "user"


# ── MCP servers and tools (Tools Registry) ─────────────────────

McpTransport = Literal["http", "sse", "stdio"]


class McpServerView(BaseModel):
    server_id: str
    owner_name: str
    name: str
    description: str = ""
    transport: McpTransport = "http"
    endpoint: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    is_active: bool = True
    last_status: str | None = None
    created_at: datetime
    updated_at: datetime


class McpServerListResponse(BaseModel):
    servers: list[McpServerView]
    count: int


class McpServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    transport: McpTransport = "http"
    endpoint: str | None = Field(default=None, max_length=1024)
    command: str | None = Field(default=None, max_length=1024)
    args: list[str] = Field(default_factory=list)
    is_active: bool = True


class McpServerUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    transport: McpTransport | None = None
    endpoint: str | None = Field(default=None, max_length=1024)
    command: str | None = Field(default=None, max_length=1024)
    args: list[str] | None = None
    is_active: bool | None = None


class McpDiscoverResponse(BaseModel):
    """Result of one discovery attempt against an MCP server."""

    ok: bool
    status: str
    tools_discovered: int = 0
    error: str | None = None


class ToolView(BaseModel):
    tool_id: str
    owner_name: str
    name: str
    description: str = ""
    source: str
    input_schema: dict = Field(default_factory=dict)
    is_enabled: bool = True
    created_at: datetime
    updated_at: datetime


class ToolListResponse(BaseModel):
    tools: list[ToolView]
    count: int


class ToolCreateRequest(BaseModel):
    """Register one tool by hand (the builtin catalog is seeded, not created)."""

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1024)
    source: str = Field(default="builtin", max_length=160)
    input_schema: dict = Field(default_factory=dict)
    is_enabled: bool = True


class ToolToggleRequest(BaseModel):
    is_enabled: bool


# ── Agent access roles ─────────────────────────────────────────


class AgentRoleView(BaseModel):
    role_name: str
    grant_type: str = "USAGE"
    verified: bool = False
    verified_at: datetime | None = None


class AgentRoleListResponse(BaseModel):
    roles: list[AgentRoleView]
    count: int


class AgentRoleAddRequest(BaseModel):
    role_name: str = Field(min_length=1, max_length=128)
    grant_type: Literal["USAGE", "OWNERSHIP"] = "USAGE"


class AccessCheckItem(BaseModel):
    """One thing the agent uses, and whether a role can reach it."""

    kind: str  # function | table | resource_group | agent
    name: str
    granted: bool
    detail: str = ""


class AccessCheckResponse(BaseModel):
    role_name: str
    all_granted: bool
    items: list[AccessCheckItem]
    checked_at: datetime


# ── Custom tools ───────────────────────────────────────────────

CustomToolKind = Literal["function", "procedure"]


class CustomToolView(BaseModel):
    tool_id: str
    owner_name: str
    name: str
    description: str = ""
    kind: CustomToolKind
    database_name: str | None = None
    function_name: str | None = None
    definition: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class CustomToolListResponse(BaseModel):
    tools: list[CustomToolView]
    count: int


class CustomToolCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    kind: CustomToolKind
    database_name: str | None = Field(default=None, max_length=128)
    function_name: str | None = Field(default=None, max_length=256)
    #: For ``function``: ``{"args": [...]}``. For ``procedure``:
    #: ``{"parameters": [{name,type,description,required}], "statements": ["SELECT ..."]}``
    definition: dict = Field(default_factory=dict)


class CustomToolUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    kind: CustomToolKind | None = None
    database_name: str | None = Field(default=None, max_length=128)
    function_name: str | None = Field(default=None, max_length=256)
    definition: dict | None = None
