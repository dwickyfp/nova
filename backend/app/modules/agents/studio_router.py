"""Nova Studio + registry API router.

Mounted at ``/api/v1/agents`` alongside the agent CRUD router. Grouped here:
the Skill Registry, the Tools Registry (builtin + MCP), and the standalone
Studio's Settings/Capabilities.

  GET    /studio/settings                 → identity + preferences
  PATCH  /studio/settings                 → update preferences
  GET    /studio/capabilities             → agents, skills, tools for the sidebar
  GET    /studio/artifacts                → saved query-backed views
  POST   /studio/artifacts                → save a chart/table definition
  POST   /studio/artifacts/{id}/refresh   → rerun its SQL with current RBAC
  GET    /tools                           → list tools (builtin seeded + MCP)
  POST   /tools                           → register a tool by hand
  PATCH  /tools/{tool_id}                 → enable/disable
  DELETE /tools/{tool_id}                 → delete a non-builtin tool
  GET    /mcp-servers                     → list MCP servers
  POST   /mcp-servers                     → add a server
  PATCH  /mcp-servers/{server_id}         → update
  DELETE /mcp-servers/{server_id}         → delete (and its tools)
  POST   /mcp-servers/{server_id}/discover→ connect and register tools

Every route requires ``get_current_user`` and is owner-scoped. The builtin tool
catalog is seeded on first read so the Tools page is never empty.
"""

# ruff: noqa: B008 — `Depends(...)` is FastAPI's DI idiom in this codebase.

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user
from app.modules.agents import mcp_client, observability, tool_catalog
from app.modules.agents.access import access_fingerprint, has_verified_access
from app.modules.agents.artifact_editor import propose_artifact_edit, run_artifact_draft
from app.modules.agents.artifact_repository import artifact_repository
from app.modules.agents.dashboard_repository import dashboard_repository
from app.modules.agents.personal_skills import (
    SkillDocumentRequest,
    parse_skill_document,
    save_skill,
)
from app.modules.agents.repository import agent_repository
from app.modules.agents.schemas import (
    AccessCheckItem,
    AccessCheckResponse,
    AgentRoleAddRequest,
    AgentRoleListResponse,
    AgentRoleView,
    AgentView,
    CustomToolCreateRequest,
    CustomToolListResponse,
    CustomToolUpdateRequest,
    CustomToolView,
    McpDiscoverResponse,
    McpServerCreateRequest,
    McpServerListResponse,
    McpServerUpdateRequest,
    McpServerView,
    SkillListResponse,
    SkillView,
    ToolCreateRequest,
    ToolListResponse,
    ToolToggleRequest,
    ToolView,
)
from app.modules.agents.studio_schemas import (
    AccessCheckRequest,
    ArtifactApplyRequest,
    ArtifactCreateRequest,
    ArtifactEditRequest,
    ArtifactEditResponse,
    ArtifactListResponse,
    ArtifactRefreshResponse,
    ArtifactSummary,
    ArtifactView,
    DashboardCreateRequest,
    DashboardListResponse,
    DashboardSummary,
    DashboardUpdateRequest,
    DashboardView,
    RoleListResponse,
    SessionListResponse,
    SessionView,
    StudioCapabilities,
    StudioPreferences,
    StudioPreferencesUpdate,
    StudioSettingsResponse,
    ThreadTraceResponse,
    UsageSummary,
)
from app.modules.agents.studio_service import studio_service
from app.modules.agents.tools.custom_tool import validate_custom_tool_definition
from app.modules.assistant.security import session_security
from app.modules.query.service import query_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Studio settings ────────────────────────────────────────────


@router.get("/studio/settings", response_model=StudioSettingsResponse)
async def get_studio_settings(user: dict = Depends(get_current_user)):
    """Identity (username, roles, warehouses) plus Studio preferences."""
    return await studio_service.settings(user)


@router.patch("/studio/settings", response_model=StudioPreferences)
async def update_studio_settings(
    body: StudioPreferencesUpdate,
    user: dict = Depends(get_current_user),
):
    return await studio_service.update_preferences(
        user["username"], body.model_dump(exclude_unset=True)
    )


@router.get("/studio/capabilities", response_model=StudioCapabilities)
async def get_studio_capabilities(user: dict = Depends(get_current_user)):
    """What the caller can use, for the Studio Capabilities view."""
    owner = user["username"]
    agents = await agent_repository.list_agents(owner_name=owner)
    role = session_security(user).active_role
    agents = [a for a in agents if await has_verified_access(a, role_name=role, user=user)]
    skills = await agent_repository.list_skills(owner_name=owner)
    await _seed_builtin_tools(owner)
    tools = await agent_repository.list_tools(owner_name=owner)
    return StudioCapabilities(
        agents=[
            {"name": a["name"], "description": a["description"], "agent_id": a["agent_id"]}
            for a in agents
        ],
        skills=[
            {
                "name": s["name"],
                "description": s["description"],
                "source": "user",
            }
            for s in skills
        ],
        tools=[
            {
                "name": t["name"],
                "description": t["description"],
                "source": t["source"],
                "is_enabled": t["is_enabled"],
            }
            for t in tools
        ],
        connectors=[
            {
                "server_id": s["server_id"],
                "name": s["name"],
                "description": s["description"],
                "is_active": s["is_active"],
                "last_status": s.get("last_status"),
            }
            for s in await agent_repository.list_mcp_servers(owner_name=INTERNAL_CONNECTOR_OWNER)
        ],
    )


@router.get("/studio/skills", response_model=SkillListResponse)
async def list_personal_skills(user: dict = Depends(get_current_user)):
    rows = await agent_repository.list_skills(owner_name=user["username"])
    return SkillListResponse(skills=[SkillView(**row) for row in rows], count=len(rows))


@router.post("/studio/skill-author", response_model=AgentView)
async def ensure_skill_author(user: dict = Depends(get_current_user)):
    from app.modules.agents.skill_author import skill_author_config

    return AgentView(**skill_author_config(user["username"]))


@router.post("/studio/skills", response_model=SkillView, status_code=201)
async def upload_personal_skill(body: SkillDocumentRequest, user: dict = Depends(get_current_user)):
    try:
        fields = parse_skill_document(body.document)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return SkillView(**await save_skill(fields, user=user))


@router.post("/studio/skills/verify")
async def verify_personal_skill(
    body: SkillDocumentRequest, user: dict = Depends(get_current_user)
) -> dict[str, str]:
    from app.modules.assistant.skill_registry import skill_library

    try:
        fields = parse_skill_document(body.document)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if skill_library.get(fields["name"]):
        raise HTTPException(409, "That name is reserved by a built-in skill.")
    existing = await agent_repository.list_skills(owner_name=user["username"])
    if any(row["name"] == fields["name"] for row in existing):
        raise HTTPException(409, "You already have a skill with this name.")
    return {"name": fields["name"], "description": fields["description"]}


@router.put("/studio/skills/{skill_id}", response_model=SkillView)
async def update_personal_skill(
    skill_id: str, body: SkillDocumentRequest, user: dict = Depends(get_current_user)
):
    try:
        fields = parse_skill_document(body.document)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return SkillView(**await save_skill(fields, user=user, skill_id=skill_id))


# ── Studio dashboards ─────────────────────────────────────────


async def _dashboard_or_404(dashboard_id: str, owner_name: str) -> dict:
    dashboard = await dashboard_repository.get(dashboard_id, owner_name=owner_name)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return dashboard


@router.get("/studio/dashboards", response_model=DashboardListResponse)
async def list_dashboards(user: dict = Depends(get_current_user)):
    rows = await dashboard_repository.list(owner_name=user["username"])
    dashboards = [DashboardSummary(**row) for row in rows]
    return DashboardListResponse(dashboards=dashboards, count=len(dashboards))


@router.post("/studio/dashboards", response_model=DashboardView, status_code=201)
async def create_dashboard(
    body: DashboardCreateRequest,
    user: dict = Depends(get_current_user),
):
    dashboard = await dashboard_repository.create(
        owner_name=user["username"], title=body.title.strip()
    )
    await write_audit_log(
        event_type="DASHBOARD",
        user_name=user["username"],
        action="create",
        object_type="DASHBOARD",
        object_name=dashboard["dashboard_id"],
        status="SUCCESS",
        session_id=user.get("session_id"),
    )
    return DashboardView(**dashboard)


@router.get("/studio/dashboards/{dashboard_id}", response_model=DashboardView)
async def get_dashboard(dashboard_id: str, user: dict = Depends(get_current_user)):
    return DashboardView(**await _dashboard_or_404(dashboard_id, user["username"]))


@router.put("/studio/dashboards/{dashboard_id}", response_model=DashboardView)
async def update_dashboard(
    dashboard_id: str,
    body: DashboardUpdateRequest,
    user: dict = Depends(get_current_user),
):
    await _dashboard_or_404(dashboard_id, user["username"])
    owned = await artifact_repository.list(owner_name=user["username"])
    owned_ids = {artifact["artifact_id"] for artifact in owned}
    if any(tile.artifact_id not in owned_ids for tile in body.layout.tiles):
        raise HTTPException(status_code=422, detail="Dashboard contains an unavailable artifact")
    updated = await dashboard_repository.update(
        dashboard_id,
        owner_name=user["username"],
        title=body.title.strip(),
        layout=body.layout,
        expected_updated_at=body.expected_updated_at,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="Dashboard changed. Reload before saving.")
    await write_audit_log(
        event_type="DASHBOARD",
        user_name=user["username"],
        action="update",
        object_type="DASHBOARD",
        object_name=dashboard_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
    )
    return DashboardView(**updated)


@router.delete("/studio/dashboards/{dashboard_id}", status_code=204)
async def delete_dashboard(dashboard_id: str, user: dict = Depends(get_current_user)):
    await _dashboard_or_404(dashboard_id, user["username"])
    await dashboard_repository.delete(dashboard_id, owner_name=user["username"])
    await write_audit_log(
        event_type="DASHBOARD",
        user_name=user["username"],
        action="delete",
        object_type="DASHBOARD",
        object_name=dashboard_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
    )


# ── Query-backed artifacts ───────────────────────────────────


def _current_role(user: dict) -> str | None:
    """Use only a role granted to the current authenticated session."""
    granted = user.get("roles") or []
    active = user.get("active_role")
    if active and active in granted:
        return active
    return None


async def _artifact_or_404(artifact_id: str, owner_name: str) -> dict:
    artifact = await artifact_repository.get(artifact_id, owner_name=owner_name)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@router.get(
    "/studio/artifacts",
    response_model=ArtifactListResponse,
    response_class=SanitizingJSONResponse,
)
async def list_artifacts(user: dict = Depends(get_current_user)):
    rows = await artifact_repository.list(owner_name=user["username"])
    artifacts = [ArtifactSummary(**row) for row in rows]
    return ArtifactListResponse(artifacts=artifacts, count=len(artifacts))


@router.post(
    "/studio/artifacts",
    response_model=ArtifactView,
    response_class=SanitizingJSONResponse,
    status_code=201,
)
async def create_artifact(
    body: ArtifactCreateRequest,
    user: dict = Depends(get_current_user),
):
    try:
        artifact = await artifact_repository.create(
            owner_name=user["username"],
            title=body.title.strip(),
            artifact_type=body.artifact_type,
            sql_text=body.sql_text,
            database_name=body.database_name,
            schema_name=body.schema_name,
            chart_spec=body.chart_spec,
            agent_id=body.agent_id,
            thread_id=body.thread_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ArtifactView(**artifact)


@router.get(
    "/studio/artifacts/{artifact_id}",
    response_model=ArtifactView,
    response_class=SanitizingJSONResponse,
)
async def get_artifact(artifact_id: str, user: dict = Depends(get_current_user)):
    artifact = await _artifact_or_404(artifact_id, user["username"])
    return ArtifactView(**artifact)


@router.post(
    "/studio/artifacts/{artifact_id}/refresh",
    response_model=ArtifactRefreshResponse,
    response_class=SanitizingJSONResponse,
)
async def refresh_artifact(
    artifact_id: str,
    user: dict = Depends(get_current_user),
):
    """Re-run the saved statement under the caller's current DB permissions."""
    artifact = await _artifact_or_404(artifact_id, user["username"])
    results = await query_service.execute_statements(
        sql=artifact["sql_text"],
        username=user["username"],
        encrypted_password=user["encrypted_password"],
        database=artifact["database_name"],
        schema=artifact["schema_name"],
        role=_current_role(user),
        max_rows=500,
        session_id=user["session_id"],
        confirm_destructive=False,
    )
    result = results[0]
    if not result.success:
        raise HTTPException(
            status_code=422,
            detail=result.error or "The artifact query could not be refreshed.",
        )
    return ArtifactRefreshResponse(
        artifact=ArtifactView(**artifact),
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        elapsed_ms=result.elapsed_ms,
    )


@router.post(
    "/studio/artifacts/{artifact_id}/edit",
    response_model=ArtifactEditResponse,
    response_class=SanitizingJSONResponse,
)
async def edit_artifact(
    artifact_id: str,
    body: ArtifactEditRequest,
    user: dict = Depends(get_current_user),
):
    artifact = await _artifact_or_404(artifact_id, user["username"])
    try:
        proposal = await propose_artifact_edit(artifact, body, user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await write_audit_log(
        event_type="ARTIFACT",
        user_name=user["username"],
        action="propose_edit",
        object_type="ARTIFACT",
        object_name=artifact_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
    )
    return proposal


@router.patch(
    "/studio/artifacts/{artifact_id}",
    response_model=ArtifactRefreshResponse,
    response_class=SanitizingJSONResponse,
)
async def apply_artifact_edit(
    artifact_id: str,
    body: ArtifactApplyRequest,
    user: dict = Depends(get_current_user),
):
    artifact = await _artifact_or_404(artifact_id, user["username"])
    try:
        result = await run_artifact_draft(body.draft, artifact, user)
        updated = await artifact_repository.update(
            artifact_id,
            owner_name=user["username"],
            expected_updated_at=body.expected_updated_at,
            sql_text=body.draft.sql_text,
            artifact_type=body.draft.artifact_type,
            chart_spec=body.draft.chart_spec,
            title=artifact["title"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="This artifact changed. Refresh it before applying the draft.",
        )
    await write_audit_log(
        event_type="ARTIFACT",
        user_name=user["username"],
        action="update",
        object_type="ARTIFACT",
        object_name=artifact_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
    )
    return ArtifactRefreshResponse(
        artifact=ArtifactView(**updated),
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        elapsed_ms=result.elapsed_ms,
    )


@router.delete("/studio/artifacts/{artifact_id}", status_code=204)
async def delete_artifact(
    artifact_id: str,
    user: dict = Depends(get_current_user),
):
    await _artifact_or_404(artifact_id, user["username"])
    await artifact_repository.delete(artifact_id, owner_name=user["username"])
    return None


# ── Tools Registry ─────────────────────────────────────────────


async def _seed_builtin_tools(owner: str) -> None:
    """Ensure the builtin catalog exists for ``owner`` (idempotent)."""
    for row in tool_catalog.builtin_rows():
        await agent_repository.upsert_tool(owner_name=owner, fields=row)


@router.get("/tools", response_model=ToolListResponse)
async def list_tools(user: dict = Depends(get_current_user)):
    await _seed_builtin_tools(user["username"])
    tools = await agent_repository.list_tools(owner_name=user["username"])
    views = [ToolView(**t) for t in tools]
    return ToolListResponse(tools=views, count=len(views))


@router.post("/tools", response_model=ToolView, status_code=201)
async def create_tool(
    body: ToolCreateRequest,
    user: dict = Depends(get_current_user),
):
    created = await agent_repository.upsert_tool(
        owner_name=user["username"], fields=body.model_dump()
    )
    return ToolView(**created)


@router.patch("/tools/{tool_id}", response_model=ToolView)
async def toggle_tool(
    tool_id: str,
    body: ToolToggleRequest,
    user: dict = Depends(get_current_user),
):
    updated = await agent_repository.set_tool_enabled(
        tool_id, owner_name=user["username"], enabled=body.is_enabled
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    return ToolView(**updated)


@router.delete("/tools/{tool_id}", status_code=204)
async def delete_tool(tool_id: str, user: dict = Depends(get_current_user)):
    tool = await agent_repository.get_tool(tool_id, owner_name=user["username"])
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    if tool["source"] == "builtin":
        raise HTTPException(
            status_code=409,
            detail="Builtin tools cannot be deleted; disable them instead.",
        )
    # A tool with no delete method: disable it. Kept simple and safe.
    await agent_repository.set_tool_enabled(tool_id, owner_name=user["username"], enabled=False)
    return None


# ── MCP servers ────────────────────────────────────────────────

INTERNAL_CONNECTOR_OWNER = "__nova__"


def _require_connector_admin(user: dict) -> None:
    if "ACCOUNTADMIN" not in (user.get("roles") or []):
        raise HTTPException(403, "MCP connectors are managed by your Nova administrator.")


async def _audit_connector(
    user: dict, action: str, server_id: str, status: str = "SUCCESS"
) -> None:
    await write_audit_log(
        event_type="MCP_CONNECTOR",
        user_name=user["username"],
        action=action,
        object_type="MCP_CONNECTOR",
        object_name=server_id,
        status=status,
        session_id=user.get("session_id"),
    )


@router.get("/mcp-servers", response_model=McpServerListResponse)
async def list_mcp_servers(user: dict = Depends(get_current_user)):
    servers = await agent_repository.list_mcp_servers(owner_name=INTERNAL_CONNECTOR_OWNER)
    if "ACCOUNTADMIN" not in (user.get("roles") or []):
        servers = [{**s, "endpoint": None, "command": None, "args": []} for s in servers]
    views = [McpServerView(**s) for s in servers]
    return McpServerListResponse(servers=views, count=len(views))


@router.post("/mcp-servers", response_model=McpServerView, status_code=201)
async def create_mcp_server(
    body: McpServerCreateRequest,
    user: dict = Depends(get_current_user),
):
    _require_connector_admin(user)
    created = await agent_repository.create_mcp_server(
        owner_name=INTERNAL_CONNECTOR_OWNER, fields=body.model_dump()
    )
    await _audit_connector(user, "CREATE", created["server_id"])
    return McpServerView(**created)


async def _require_server(server_id: str, owner: str) -> dict:
    server = await agent_repository.get_mcp_server(server_id, owner_name=owner)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return server


@router.patch("/mcp-servers/{server_id}", response_model=McpServerView)
async def update_mcp_server(
    server_id: str,
    body: McpServerUpdateRequest,
    user: dict = Depends(get_current_user),
):
    _require_connector_admin(user)
    await _require_server(server_id, INTERNAL_CONNECTOR_OWNER)
    updated = await agent_repository.update_mcp_server(
        server_id, owner_name=INTERNAL_CONNECTOR_OWNER, fields=body.model_dump(exclude_unset=True)
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    await _audit_connector(user, "UPDATE", server_id)
    return McpServerView(**updated)


@router.delete("/mcp-servers/{server_id}", status_code=204)
async def delete_mcp_server(server_id: str, user: dict = Depends(get_current_user)):
    _require_connector_admin(user)
    if not await agent_repository.delete_mcp_server(server_id, owner_name=INTERNAL_CONNECTOR_OWNER):
        raise HTTPException(status_code=404, detail="MCP server not found")
    await _audit_connector(user, "DELETE", server_id)
    return None


@router.post("/mcp-servers/{server_id}/discover", response_model=McpDiscoverResponse)
async def discover_mcp_server(
    server_id: str,
    user: dict = Depends(get_current_user),
):
    """Connect to the server and register the tools it exposes.

    Metadata only: the tools are recorded so they can be listed and selected.
    Nova does not invoke them, and does not run stdio servers.
    """
    _require_connector_admin(user)
    owner = INTERNAL_CONNECTOR_OWNER
    server = await _require_server(server_id, owner)

    try:
        discovered = await mcp_client.list_tools(server)
    except mcp_client.McpError as exc:
        await agent_repository.update_mcp_server(
            server_id, owner_name=owner, fields={"last_status": "error"}
        )
        await _audit_connector(user, "DISCOVER", server_id, "FAILED")
        return McpDiscoverResponse(ok=False, status="error", error=str(exc))

    for tool in discovered:
        await agent_repository.upsert_tool(
            owner_name=owner,
            fields={
                "name": tool["name"],
                "description": tool["description"],
                "source": f"mcp:{server_id}",
                "input_schema": tool["input_schema"],
                "is_enabled": True,
            },
        )
    await agent_repository.update_mcp_server(
        server_id, owner_name=owner, fields={"last_status": "connected"}
    )
    await _audit_connector(user, "DISCOVER", server_id)
    return McpDiscoverResponse(ok=True, status="connected", tools_discovered=len(discovered))


# ── Available roles (for the Access dropdown) ──────────────────


@router.get("/roles", response_model=RoleListResponse)
async def list_available_roles(user: dict = Depends(get_current_user)):
    """Every role on the engine, for an Access picker.

    Any authenticated user may read the names: they are not secrets, and the
    Access tab needs the full list to offer a dropdown rather than free text.
    The caller's own roles are returned separately by ``/studio/settings``.
    """
    from app.modules.users.service import user_service

    try:
        roles = await user_service.list_roles()
    except Exception:  # noqa: BLE001 - an empty list is a safe fallback
        logger.warning("Could not list roles for the Access picker")
        return RoleListResponse(roles=[], count=0)
    names = [r["name"] for r in roles if r.get("name")]
    return RoleListResponse(roles=names, count=len(names))


# ── Agent access roles + Verify Access ─────────────────────────
#
# These are agent-scoped, so they live under /{agent_id}. Declared in the studio
# router, which is included before the agent router; the dynamic agent routes in
# the agent router do not define these sub-paths, so there is no collision.


@router.get("/{agent_id}/access", response_model=AgentRoleListResponse)
async def list_agent_access(agent_id: str, user: dict = Depends(get_current_user)):
    agent = await _require_agent(agent_id, user["username"])
    roles = await agent_repository.list_agent_roles(agent_id, owner_name=user["username"])
    fingerprint = await access_fingerprint(agent)
    views = []
    for grant in roles:
        valid = grant.get("verified_fingerprint") == fingerprint
        if valid:
            valid = await has_verified_access(
                agent, role_name=grant["role_name"], user=user
            )
        views.append(AgentRoleView(
            role_name=grant["role_name"], grant_type=grant["grant_type"],
            verified=valid, verified_at=grant.get("verified_at") if valid else None,
        ))
    return AgentRoleListResponse(roles=views, count=len(views))


@router.post("/{agent_id}/access", response_model=AgentRoleView, status_code=201)
async def add_agent_access(
    agent_id: str,
    body: AgentRoleAddRequest,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user["username"])
    from app.modules.users.service import user_service

    available = await user_service.list_roles()
    if body.role_name not in {r["name"] for r in available if r.get("name")}:
        raise HTTPException(status_code=422, detail="Role does not exist")
    await agent_repository.add_agent_role(
        agent_id,
        owner_name=user["username"],
        role_name=body.role_name,
        grant_type=body.grant_type,
    )
    await write_audit_log(
        event_type="AGENT_ACCESS",
        user_name=user["username"],
        action="GRANT",
        object_type="AGENT",
        object_name=agent_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )
    return AgentRoleView(role_name=body.role_name, grant_type=body.grant_type)


@router.delete("/{agent_id}/access/{role_name}", status_code=204)
async def remove_agent_access(
    agent_id: str,
    role_name: str,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user["username"])
    if not await agent_repository.remove_agent_role(
        agent_id, owner_name=user["username"], role_name=role_name
    ):
        raise HTTPException(status_code=404, detail="Role not granted on this agent")
    await write_audit_log(
        event_type="AGENT_ACCESS",
        user_name=user["username"],
        action="REVOKE",
        object_type="AGENT",
        object_name=agent_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )
    return None


@router.post("/{agent_id}/access/verify", response_model=AccessCheckResponse)
async def verify_agent_access(
    agent_id: str,
    body: AccessCheckRequest,
    user: dict = Depends(get_current_user),
):
    """Check a role can reach everything this agent uses.

    Resolves the agent's dependencies and checks each against Nova-managed
    Ranger policy state. Native StarRocks marker-role grants are not consulted.
    """
    from app.modules.agents.access import verify_access

    agent = await _require_agent(agent_id, user["username"])
    grants = await agent_repository.list_agent_roles(agent_id, owner_name=user["username"])
    if body.role_name not in {grant["role_name"] for grant in grants}:
        raise HTTPException(status_code=422, detail="Assign the role before verifying access")
    try:
        items = await verify_access(
            agent=agent,
            role_name=body.role_name,
            username=user["username"],
            encrypted_password=user.get("encrypted_password", ""),
            session_id=user.get("session_id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    all_granted = all(item.granted for item in items)
    await agent_repository.set_agent_role_verification(
        agent_id,
        owner_name=user["username"],
        role_name=body.role_name,
        fingerprint=await access_fingerprint(agent) if all_granted else None,
    )
    await write_audit_log(
        event_type="AGENT_ACCESS",
        user_name=user["username"],
        action="VERIFY",
        object_type="AGENT",
        object_name=agent_id,
        status="SUCCESS" if all_granted else "DENIED",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )
    return AccessCheckResponse(
        role_name=body.role_name,
        all_granted=all_granted,
        items=[AccessCheckItem(**item.__dict__) for item in items],
        checked_at=datetime.now(),
    )


# ── Custom tools ───────────────────────────────────────────────


@router.get("/custom-tools", response_model=CustomToolListResponse)
async def list_custom_tools(user: dict = Depends(get_current_user)):
    tools = await agent_repository.list_custom_tools(owner_name=user["username"])
    views = [CustomToolView(**t) for t in tools]
    return CustomToolListResponse(tools=views, count=len(views))


@router.post("/custom-tools", response_model=CustomToolView, status_code=201)
async def create_custom_tool(
    body: CustomToolCreateRequest,
    user: dict = Depends(get_current_user),
):
    fields = body.model_dump()
    errors = validate_custom_tool_definition(fields)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    await _require_unique_custom_tool_name(user["username"], fields["name"])
    created = await agent_repository.create_custom_tool(owner_name=user["username"], fields=fields)
    await write_audit_log(
        event_type="CUSTOM_TOOL",
        user_name=user["username"],
        action="create",
        object_type="CUSTOM_TOOL",
        object_name=created["tool_id"],
        status="SUCCESS",
        session_id=user.get("session_id"),
    )
    return CustomToolView(**created)


@router.get("/custom-tools/{tool_id}", response_model=CustomToolView)
async def get_custom_tool(tool_id: str, user: dict = Depends(get_current_user)):
    tool = await agent_repository.get_custom_tool(tool_id, owner_name=user["username"])
    if tool is None:
        raise HTTPException(status_code=404, detail="Custom tool not found")
    return CustomToolView(**tool)


@router.put("/custom-tools/{tool_id}", response_model=CustomToolView)
async def update_custom_tool(
    tool_id: str,
    body: CustomToolUpdateRequest,
    user: dict = Depends(get_current_user),
):
    existing = await _require_custom_tool(tool_id, user["username"])
    changes = body.model_dump(exclude_unset=True)
    candidate = {**existing, **changes}
    errors = validate_custom_tool_definition(candidate)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    if candidate["name"] != existing["name"]:
        await _require_unique_custom_tool_name(
            user["username"], candidate["name"], except_tool_id=tool_id
        )
    updated = await agent_repository.update_custom_tool(
        tool_id, owner_name=user["username"], fields=changes
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Custom tool not found")
    if updated["name"] != existing["name"]:
        old_key = f"custom:{existing['name']}"
        new_key = f"custom:{updated['name']}"
        agents = await agent_repository.list_agents(owner_name=user["username"])
        for agent in agents:
            selected = agent.get("default_tools") or []
            if old_key in selected:
                await agent_repository.update_agent(
                    agent["agent_id"],
                    owner_name=user["username"],
                    fields={
                        "default_tools": [new_key if tool == old_key else tool for tool in selected]
                    },
                )
    await write_audit_log(
        event_type="CUSTOM_TOOL",
        user_name=user["username"],
        action="update",
        object_type="CUSTOM_TOOL",
        object_name=tool_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
    )
    return CustomToolView(**updated)


@router.delete("/custom-tools/{tool_id}", status_code=204)
async def delete_custom_tool(tool_id: str, user: dict = Depends(get_current_user)):
    if not await agent_repository.delete_custom_tool(tool_id, owner_name=user["username"]):
        raise HTTPException(status_code=404, detail="Custom tool not found")
    await write_audit_log(
        event_type="CUSTOM_TOOL",
        user_name=user["username"],
        action="delete",
        object_type="CUSTOM_TOOL",
        object_name=tool_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
    )
    return None


# ── Observability ──────────────────────────────────────────────


@router.get("/{agent_id}/observability/usage", response_model=UsageSummary)
async def agent_usage(agent_id: str, days: int = 7, user: dict = Depends(get_current_user)):
    await _require_agent(agent_id, user["username"])
    data = await observability.usage_summary(
        owner_name=user["username"], agent_id=agent_id, days=_clamp_days(days)
    )
    return UsageSummary(**data)


@router.get("/{agent_id}/observability/sessions", response_model=SessionListResponse)
async def agent_sessions(agent_id: str, limit: int = 100, user: dict = Depends(get_current_user)):
    await _require_agent(agent_id, user["username"])
    sessions = await observability.list_sessions(
        owner_name=user["username"], agent_id=agent_id, limit=min(max(limit, 1), 500)
    )
    return SessionListResponse(sessions=[SessionView(**s) for s in sessions])


@router.get(
    "/{agent_id}/observability/sessions/{thread_id}",
    response_model=ThreadTraceResponse,
)
async def agent_thread_trace(agent_id: str, thread_id: str, user: dict = Depends(get_current_user)):
    await _require_agent(agent_id, user["username"])
    trace = await observability.thread_trace(
        owner_name=user["username"], agent_id=agent_id, thread_id=thread_id
    )
    if trace is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return ThreadTraceResponse(**trace)


async def _require_agent(agent_id: str, owner: str) -> dict:
    agent = await agent_repository.get_agent(agent_id, owner_name=owner)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


async def _require_custom_tool(tool_id: str, owner: str) -> dict:
    tool = await agent_repository.get_custom_tool(tool_id, owner_name=owner)
    if tool is None:
        raise HTTPException(status_code=404, detail="Custom tool not found")
    return tool


async def _require_unique_custom_tool_name(
    owner: str, name: str, *, except_tool_id: str | None = None
) -> None:
    tools = await agent_repository.list_custom_tools(owner_name=owner)
    if any(tool["name"] == name and tool["tool_id"] != except_tool_id for tool in tools):
        raise HTTPException(status_code=409, detail="Custom tool name already exists")


def _clamp_days(days: int) -> int:
    return min(max(days, 1), 90)
