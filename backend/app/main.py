"""Nova Backend — FastAPI App Factory.

StarRocks management console backend with domain-driven modular architecture.
"""

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.common.nova_system import init_nova_system
from app.common.secret_keys import require_configured_secrets
from app.core.config import settings
from app.core.database import db
from app.core.exceptions import register_exception_handlers
from app.core.redis import session_store
from app.modules.access_control.router import router as access_control_router

# --- Module routers ---
from app.modules.agents.router import router as agents_router
from app.modules.agents.studio_router import router as studio_router
from app.modules.ai_ml.router import router as ai_router
from app.modules.assistant.router import router as assistant_router
from app.modules.auth.router import router as auth_router
from app.modules.backup.router import router as backup_router
from app.modules.explorer.router import router as explorer_router
from app.modules.external_catalogs.router import router as external_catalogs_router
from app.modules.functions.router import router as functions_router
from app.modules.governance.router import router as governance_router
from app.modules.indexes.router import router as indexes_router
from app.modules.llm_functions.router import router as llm_fn_router
from app.modules.migration.router import router as migration_router
from app.modules.ml_engine.internal_router import router as ml_internal_router
from app.modules.ml_engine.router import router as ml_router
from app.modules.monitoring.router import router as monitoring_router
from app.modules.objects.router import router as objects_router
from app.modules.pipes.router import router as pipes_router
from app.modules.query.router import router as query_router
from app.modules.resource_groups.router import router as resource_groups_router
from app.modules.stages.router import router as stages_router
from app.modules.system.router import router as system_router
from app.modules.tables.router import router as tables_router
from app.modules.task_orchestration.router import router as task_orchestration_router
from app.modules.tasks.router import router as tasks_router
from app.modules.users.router import router as users_router
from app.modules.variables.router import router as variables_router
from app.modules.views.router import router as views_router
from app.modules.workspaces.router import router as workspaces_router

logger = logging.getLogger(__name__)
# from app.modules.query.router import router as query_router
# from app.modules.objects.router import router as objects_router
# from app.modules.tables.router import router as tables_router
# from app.modules.views.router import router as views_router
# from app.modules.external_catalogs.router import router as ext_router
# from app.modules.cluster.router import router as cluster_router
# from app.modules.dashboards.router import router as dash_router
# from app.modules.system.router import router as sys_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Fail fast on missing/placeholder signing and encryption keys (NOVA-108).
    # This runs before any connection is opened so a misconfigured deployment
    # dies at boot with a clear message instead of at first login.
    require_configured_secrets()

    # Startup
    await db.init_system_pool()
    # Align the engine's global time_zone with Nova's session pin (advisory; the
    # per-session init_command is the guarantee). Best-effort inside the method.
    await db.apply_global_time_zone()
    await session_store.init()
    await init_nova_system()

    # Workspace object storage (NOVA-137). Idempotent: creates the configured
    # bucket if absent so the first "create file" does not fail with
    # NoSuchBucket. Best-effort and non-fatal — a missing bucket only breaks
    # the workspace feature, and the request path still classifies the failure
    # into a 503 with an actionable message.
    try:
        from app.modules.workspaces.storage_bootstrap import ensure_workspace_bucket

        ensure_workspace_bucket()
    except Exception as e:
        logger.warning("Could not ensure workspace storage bucket: %s", e)

    # Nova-managed external catalog metadata (NOVA-62). Best-effort: the engine
    # catalog is the source of truth, and a missing mirror table only degrades
    # the list endpoint, not the service.
    try:
        from app.modules.external_catalogs.repository import external_catalog_repo

        await external_catalog_repo.ensure_schema()
    except Exception as e:
        logger.warning("Could not ensure external catalog schema: %s", e)

    # Migration Connector source registry (Phase 11 v1). Best-effort, same
    # reasoning: a missing mirror table only degrades the source list, not the
    # read-only assessment path.
    try:
        from app.modules.migration.repository import migration_repo

        await migration_repo.ensure_schema()
    except Exception as e:
        logger.warning("Could not ensure migration source schema: %s", e)

    # Assistant conversation storage. Threads/messages used to live in process
    # memory and vanished on reload; persisting them per user is what survives a
    # restart. Best-effort: without it the assistant still answers, but history
    # is not durable.
    try:
        from app.modules.assistant.repository import assistant_repository

        await assistant_repository.ensure_schema()
    except Exception as e:
        logger.warning("Could not ensure assistant conversation schema: %s", e)

    # Agent Studio storage (Phase 12): agents, semantic models, user skills.
    # Best-effort in the same way: without it the routes answer 500 rather than
    # taking the whole web service down at boot.
    try:
        from app.modules.agents.artifact_repository import artifact_repository
        from app.modules.agents.dashboard_repository import dashboard_repository
        from app.modules.agents.memory import memory_repository
        from app.modules.agents.repository import agent_repository
        from app.modules.agents.rule_proposals import rule_proposal_repository
        from app.modules.agents.run_journal import run_journal

        await agent_repository.ensure_schema()
        await memory_repository.ensure_schema()
        await rule_proposal_repository.ensure_schema()
        await run_journal.ensure_schema()
        await agent_repository.migrate_legacy_skill_authors()
        await artifact_repository.ensure_schema()
        await dashboard_repository.ensure_schema()
    except Exception as e:
        logger.warning("Could not ensure Agent Studio schema: %s", e)

    # Register LLM function UDFs (AI_COMPLETE, AI_SENTIMENT, etc.)
    # so they are available as SQL functions from the start.
    try:
        from app.modules.llm_functions.service import llm_function_service

        result = await llm_function_service.register_all_udfs()
        logger.info(
            "LLM UDFs registered: %d ok, %d failed",
            result["registered"],
            result["failed"],
        )
    except Exception as e:
        logger.warning("Failed to register LLM UDFs on startup: %s", e)

    # MySQL protocol proxy. Embedded rather than a second process so the web
    # service alone is enough to serve port 4406; `python -m app.proxy` runs the
    # same server standalone. A proxy that cannot bind (port already taken by a
    # standalone proxy, most likely) must not take the web service down with it.
    proxy_server = None
    if settings.PROXY_ENABLED:
        try:
            from app.proxy.server import MySQLProxyServer

            proxy_server = MySQLProxyServer()
            await proxy_server.start()
        except Exception as e:
            logger.warning("MySQL proxy did not start: %s", e)
            proxy_server = None

    from app.modules.ml_engine.service import ml_engine_service

    await ml_engine_service.ephemeral_repository.ensure_schema()
    ml_cleanup = asyncio.create_task(ml_engine_service.sweep_ephemeral())
    try:
        yield
    finally:
        ml_cleanup.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ml_cleanup
    # Shutdown
    if proxy_server is not None:
        try:
            await proxy_server.stop()
        except Exception as e:
            logger.warning("MySQL proxy did not stop cleanly: %s", e)
    await session_store.close()
    await db.close_system_pool()


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title="Nova",
        version="0.1.0",
        description="Management console backend for StarRocks",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Nova-Run-ID"],
    )

    # Exception handlers
    register_exception_handlers(app)

    # API v1 routers
    prefix = "/api/v1"
    app.include_router(auth_router, prefix=f"{prefix}/auth", tags=["auth"])
    app.include_router(query_router, prefix=f"{prefix}/query", tags=["query"])
    app.include_router(objects_router, prefix=f"{prefix}/objects", tags=["objects"])
    app.include_router(tables_router, prefix=f"{prefix}/tables", tags=["tables"])
    app.include_router(indexes_router, prefix=f"{prefix}/indexes", tags=["indexes"])
    app.include_router(views_router, prefix=f"{prefix}/views", tags=["views"])
    app.include_router(stages_router, prefix=f"{prefix}/stages", tags=["stages"])
    app.include_router(explorer_router, prefix=f"{prefix}/explorer", tags=["explorer"])
    app.include_router(system_router, prefix=f"{prefix}/system", tags=["system"])
    app.include_router(users_router, prefix=f"{prefix}/users", tags=["users"])
    app.include_router(
        access_control_router,
        prefix=f"{prefix}/access-control",
        tags=["access-control"],
    )
    app.include_router(ai_router, prefix=f"{prefix}/ai", tags=["ai"])
    app.include_router(llm_fn_router, prefix=f"{prefix}/ai", tags=["ai"])
    app.include_router(ml_router, prefix=f"{prefix}/ml", tags=["ml"])
    app.include_router(ml_internal_router, prefix=f"{prefix}/internal/ml", tags=["internal"])
    app.include_router(workspaces_router, prefix=f"{prefix}/workspaces", tags=["workspaces"])
    app.include_router(monitoring_router, prefix=f"{prefix}/monitoring", tags=["monitoring"])
    app.include_router(functions_router, prefix=f"{prefix}/functions", tags=["functions"])
    # Dynamic data masking + row access policies (roadmap #3/#4). RBAC-native:
    # every statement runs on the caller's connection.
    app.include_router(governance_router, prefix=f"{prefix}/governance", tags=["governance"])
    # Resource groups / warehouses (roadmap #16). Quota enforcement is the
    # engine's; Nova only configures and reads back its usage.
    app.include_router(
        resource_groups_router,
        prefix=f"{prefix}/resource-groups",
        tags=["resource-groups"],
    )
    # Session/global variables browser + SET (roadmap #8).
    app.include_router(variables_router, prefix=f"{prefix}/variables", tags=["variables"])
    # Backup / restore / recycle bin (roadmap #7). Mutations are gated to
    # backup-admin roles in the router; the engine's REPOSITORY privilege is the
    # second gate because every statement runs on the caller's connection.
    app.include_router(backup_router, prefix=f"{prefix}/backup", tags=["backup"])
    app.include_router(tasks_router, prefix=f"{prefix}/tasks", tags=["tasks"])
    # Nova orchestration metadata (CREATE TASK graphs/runs) — distinct from
    # `/tasks`, which reads StarRocks' native task surface. Read-only.
    app.include_router(
        task_orchestration_router,
        prefix=f"{prefix}/task-orchestration",
        tags=["task-orchestration"],
    )
    app.include_router(pipes_router, prefix=f"{prefix}/pipes", tags=["pipes"])
    # Phase 10 — bounded agentic assistant (NOVA-61). Thread state is
    # process-local (E5a); see docs/specs/nova-61-agentic-assistant-design.md.
    app.include_router(assistant_router, prefix=f"{prefix}/assistant", tags=["assistant"])
    # Phase 12 — Agent Studio: build-your-own agents, semantic models (Ossie),
    # and user skills, plus the Nova Studio run surface. Composes the Phase 10
    # assistant loop rather than modifying it. See
    # docs/specs/nova-12-agent-studio-implementation-plan.md.
    # Nova Studio settings, capabilities, and the Skill/Tools registries.
    # Registered BEFORE the agent router: it has literal paths ("/tools",
    # "/mcp-servers", "/studio/...") that the agent router's dynamic
    # "/{agent_id}" would otherwise capture. FastAPI matches in declaration
    # order, so the literal routes must come first.
    app.include_router(studio_router, prefix=f"{prefix}/agents", tags=["agents"])
    app.include_router(agents_router, prefix=f"{prefix}/agents", tags=["agents"])
    # External catalogs (Iceberg + Hive, GA-only) — NOVA-62 / Phase 0 #9.
    app.include_router(
        external_catalogs_router,
        prefix=f"{prefix}/external-catalogs",
        tags=["external-catalogs"],
    )
    # Phase 11 — Migration Connector: assess, dry-run, plan, and execute.
    # Execute is gated on #7 (backup/restore): the endpoint exists but refuses
    # (403) unless the operator sets MIGRATION_EXECUTE_ENABLED. Data movement is
    # not implemented (11-C).
    app.include_router(migration_router, prefix=f"{prefix}/migration", tags=["migration"])

    # Static files for Java UDFs
    udf_dir = os.path.join(os.path.dirname(__file__), "static", "udf")
    os.makedirs(udf_dir, exist_ok=True)
    app.mount("/static/udf", StaticFiles(directory=udf_dir), name="udf_static")

    # Future modules:
    # app.include_router(query_router, prefix=f"{prefix}/query", tags=["query"])
    # app.include_router(objects_router, prefix=f"{prefix}/objects", tags=["objects"])
    # stages_router registered above
    # app.include_router(users_router, prefix=f"{prefix}/users", tags=["users"])
    # ... etc

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    return app


app = create_app()
