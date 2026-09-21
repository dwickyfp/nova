"""Nova Studio + registry API router.

Mounted at ``/api/v1/agents`` alongside the agent CRUD router. Grouped here:
the Skill Registry, the Tools Registry (builtin + MCP), and the standalone
Studio's Settings/Capabilities.

  GET    /studio/settings                 → identity + preferences
  PATCH  /studio/settings                 → update preferences
  GET    /studio/capabilities             → agents, skills, tools for the sidebar
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

from app.core.deps import get_current_user
from app.modules.agents import mcp_client, observability, tool_catalog
from app.modules.agents.repository import agent_repository
from app.modules.agents.schemas import (
    AccessCheckItem,
    AccessCheckResponse,
    AgentRoleAddRequest,
    AgentRoleListResponse,
    AgentRoleView,
    CustomToolCreateRequest,
    CustomToolListResponse,
    CustomToolUpdateRequest,
    CustomToolView,
    McpDiscoverResponse,
    McpServerCreateRequest,
    McpServerListResponse,
    McpServerUpdateRequest,
    McpServerView,
    ToolCreateRequest,
    ToolListResponse,
    ToolToggleRequest,
    ToolView,
)
from app.modules.agents.studio_schemas import (
    AccessCheckRequest,
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
    skills = await agent_repository.list_skills(owner_name=owner)
    await _seed_builtin_tools(owner)
    tools = await agent_repository.list_tools(owner_name=owner)
    return StudioCapabilities(
        agents=[
            {"name": a["name"], "description": a["description"], "agent_id": a["agent_id"]}
            for a in agents
        ],
        skills=[
            {"name": s["name"], "description": s["description"]} for s in skills
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
    )


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
    await agent_repository.set_tool_enabled(
        tool_id, owner_name=user["username"], enabled=False
    )
    return None


# ── MCP servers ────────────────────────────────────────────────


@router.get("/mcp-servers", response_model=McpServerListResponse)
async def list_mcp_servers(user: dict = Depends(get_current_user)):
    servers = await agent_repository.list_mcp_servers(owner_name=user["username"])
    views = [McpServerView(**s) for s in servers]
    return McpServerListResponse(servers=views, count=len(views))


@router.post("/mcp-servers", response_model=McpServerView, status_code=201)
async def create_mcp_server(
    body: McpServerCreateRequest,
    user: dict = Depends(get_current_user),
):
    created = await agent_repository.create_mcp_server(
        owner_name=user["username"], fields=body.model_dump()
    )
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
    await _require_server(server_id, user["username"])
    updated = await agent_repository.update_mcp_server(
        server_id, owner_name=user["username"], fields=body.model_dump(exclude_unset=True)
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return McpServerView(**updated)


@router.delete("/mcp-servers/{server_id}", status_code=204)
async def delete_mcp_server(server_id: str, user: dict = Depends(get_current_user)):
    if not await agent_repository.delete_mcp_server(
        server_id, owner_name=user["username"]
    ):
        raise HTTPException(status_code=404, detail="MCP server not found")
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
    owner = user["username"]
    server = await _require_server(server_id, owner)

    try:
        discovered = await mcp_client.list_tools(server)
    except mcp_client.McpError as exc:
        await agent_repository.update_mcp_server(
            server_id, owner_name=owner, fields={"last_status": "error"}
        )
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
    return McpDiscoverResponse(
        ok=True, status="connected", tools_discovered=len(discovered)
    )


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
async def list_agent_access(
    agent_id: str, user: dict = Depends(get_current_user)
):
    await _require_agent(agent_id, user["username"])
    roles = await agent_repository.list_agent_roles(
        agent_id, owner_name=user["username"]
    )
    views = [AgentRoleView(**r) for r in roles]
    return AgentRoleListResponse(roles=views, count=len(views))


@router.post("/{agent_id}/access", response_model=AgentRoleView, status_code=201)
async def add_agent_access(
    agent_id: str,
    body: AgentRoleAddRequest,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user["username"])
    await agent_repository.add_agent_role(
        agent_id,
        owner_name=user["username"],
        role_name=body.role_name,
        grant_type=body.grant_type,
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
    return None


@router.post("/{agent_id}/access/verify", response_model=AccessCheckResponse)
async def verify_agent_access(
    agent_id: str,
    body: AccessCheckRequest,
    user: dict = Depends(get_current_user),
):
    """Check a role can reach everything this agent uses.

    Resolves the agent's dependencies (custom function tools, the tables behind
    its semantic model, its database) and checks each against the role's engine
    grants via ``SHOW GRANTS``. Read-only.
    """
    from app.modules.agents.access import verify_access

    agent = await _require_agent(agent_id, user["username"])
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

    return AccessCheckResponse(
        role_name=body.role_name,
        all_granted=all(item.granted for item in items),
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
    created = await agent_repository.create_custom_tool(
        owner_name=user["username"], fields=body.model_dump()
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
    await _require_custom_tool(tool_id, user["username"])
    updated = await agent_repository.update_custom_tool(
        tool_id, owner_name=user["username"], fields=body.model_dump(exclude_unset=True)
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Custom tool not found")
    return CustomToolView(**updated)


@router.delete("/custom-tools/{tool_id}", status_code=204)
async def delete_custom_tool(tool_id: str, user: dict = Depends(get_current_user)):
    if not await agent_repository.delete_custom_tool(
        tool_id, owner_name=user["username"]
    ):
        raise HTTPException(status_code=404, detail="Custom tool not found")
    return None


# ── Observability ──────────────────────────────────────────────


@router.get("/{agent_id}/observability/usage", response_model=UsageSummary)
async def agent_usage(
    agent_id: str, days: int = 7, user: dict = Depends(get_current_user)
):
    await _require_agent(agent_id, user["username"])
    data = await observability.usage_summary(
        owner_name=user["username"], agent_id=agent_id, days=_clamp_days(days)
    )
    return UsageSummary(**data)


@router.get("/{agent_id}/observability/sessions", response_model=SessionListResponse)
async def agent_sessions(
    agent_id: str, limit: int = 100, user: dict = Depends(get_current_user)
):
    await _require_agent(agent_id, user["username"])
    sessions = await observability.list_sessions(
        owner_name=user["username"], agent_id=agent_id, limit=min(max(limit, 1), 500)
    )
    return SessionListResponse(sessions=[SessionView(**s) for s in sessions])


@router.get(
    "/{agent_id}/observability/sessions/{thread_id}",
    response_model=ThreadTraceResponse,
)
async def agent_thread_trace(
    agent_id: str, thread_id: str, user: dict = Depends(get_current_user)
):
    await _require_agent(agent_id, user["username"])
    trace = await observability.thread_trace(
        owner_name=user["username"], thread_id=thread_id
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


def _clamp_days(days: int) -> int:
    return min(max(days, 1), 90)
