"""Nova Backend — FastAPI App Factory.

Nova Enterprise Intelligence OS backend with domain-driven modular architecture.
"""

import asyncio
import contextlib
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.common.nova_system import init_nova_system
from app.common.secret_keys import require_configured_secrets
from app.core.config import settings
from app.core.database import db
from app.core.exceptions import register_exception_handlers
from app.core.metadata_process import (
    check_metadata,
    close_process_metadata,
    open_process_metadata,
)
from app.core.redis import session_store
from app.core.studio_capabilities import log_studio_capabilities
from app.modules.access_control.router import router as access_control_router
from app.modules.agents.knowledge_router import router as knowledge_router

# --- Module routers ---
from app.modules.agents.router import router as agents_router
from app.modules.agents.studio_router import router as studio_router
from app.modules.ai_ml.router import router as ai_router
from app.modules.assistant.router import router as assistant_router
from app.modules.auth.router import router as auth_router
from app.modules.backup.router import router as backup_router
from app.modules.catalog.connections_router import router as catalog_connections_router
from app.modules.catalog.metadata_router import router as catalog_metadata_router
from app.modules.catalog.router import router as catalog_router
from app.modules.explorer.router import router as explorer_router
from app.modules.external_catalogs.router import router as external_catalogs_router
from app.modules.functions.router import router as functions_router
from app.modules.indexes.router import router as indexes_router
from app.modules.intelligence.autopilot import router as semantic_autopilot_router
from app.modules.intelligence.business_events_router import router as business_events_router
from app.modules.intelligence.engine_router import router as intelligence_engine_router
from app.modules.intelligence.entities import router as entities_router
from app.modules.intelligence.feature_store import router as feature_store_router
from app.modules.intelligence.search import router as search_router
from app.modules.intelligence.semantic_views import router as semantic_views_router
from app.modules.llm_functions.gateway import llm_gateway
from app.modules.llm_functions.gateway import router as llm_gateway_router
from app.modules.llm_functions.router import router as llm_fn_router
from app.modules.migration.router import router as migration_router
from app.modules.ml_engine.internal_router import router as ml_internal_router
from app.modules.ml_engine.router import router as ml_router
from app.modules.monitoring.router import router as monitoring_router
from app.modules.notebooks.router import router as notebooks_router
from app.modules.objects.router import router as objects_router
from app.modules.ontology.router import router as ontology_router
from app.modules.pipes.router import router as pipes_router
from app.modules.query.router import router as query_router
from app.modules.query_autopilot.router import router as query_autopilot_router
from app.modules.resource_groups.router import router as resource_groups_router
from app.modules.stages.router import router as stages_router
from app.modules.streams.router import router as streams_router
from app.modules.superset.dataviz_router import router as dataviz_router
from app.modules.superset.router import internal_router as superset_internal_router
from app.modules.superset.router import router as superset_router
from app.modules.system.router import router as system_router
from app.modules.tables.router import router as tables_router
from app.modules.task_orchestration.router import router as task_orchestration_router
from app.modules.tasks.router import router as tasks_router
from app.modules.users.router import router as users_router
from app.modules.variables.router import router as variables_router
from app.modules.views.router import router as views_router
from app.modules.workspaces.router import router as workspaces_router
from app.observability.http import HTTPMetricsMiddleware, metrics_response
from app.observability.metrics import PROXY_EXPECTED, SERVICE_UP

logger = logging.getLogger(__name__)
# from app.modules.query.router import router as query_router
# from app.modules.objects.router import router as objects_router
# from app.modules.tables.router import router as tables_router
# from app.modules.views.router import router as views_router
# from app.modules.external_catalogs.router import router as ext_router
# from app.modules.cluster.router import router as cluster_router
# from app.modules.dashboards.router import router as dash_router
# from app.modules.system.router import router as sys_router


#: Held while one process re-registers the global LLM functions. Registration
#: drops and recreates each function, so two processes doing it at once would
#: leave a window in which a function a query needs does not exist.

#: Held by the one process that is creating or migrating ``NOVA_SYSTEM``.
_BOOTSTRAP_LOCK = "nova:control-plane:bootstrap"
BOOTSTRAP_LOCK_TTL_SECONDS = 600
BOOTSTRAP_LOCK_POLL_SECONDS = 1.0

#: A dependency that has not answered in this long is not ready to serve.
READINESS_TIMEOUT_SECONDS = 3


def _owns_control_plane(role: str) -> bool:
    """Whether this process bootstraps schemas and registers LLM functions."""
    return role != "query"


def _service_label(role: str) -> str:
    """The ``nova_service_up`` label; the query tier reports separately."""
    return "query" if role == "query" else "backend"


async def _bootstrap_exclusively() -> None:
    """Run the schema bootstrap in one process at a time.

    Most of it is ``CREATE TABLE IF NOT EXISTS``, but a few steps migrate or
    backfill data and are not safe when two processes interleave. A process
    that finds the lock held waits, then runs the bootstrap itself, by which
    time every step is a no-op.
    """
    from app.modules.task_orchestration.transport import LeaderLock

    try:
        lock = LeaderLock(
            session_store._redis, key=_BOOTSTRAP_LOCK, ttl_seconds=BOOTSTRAP_LOCK_TTL_SECONDS
        )
        deadline = time.monotonic() + BOOTSTRAP_LOCK_TTL_SECONDS
        while not await lock.acquire():
            if time.monotonic() >= deadline:
                raise RuntimeError("Schema bootstrap lock deadline exceeded")
            await asyncio.sleep(BOOTSTRAP_LOCK_POLL_SECONDS)
    except Exception:
        raise RuntimeError("Schema bootstrap requires an exclusive lock") from None
    try:
        await _bootstrap_control_plane()
    finally:
        with contextlib.suppress(Exception):
            await lock.release()


async def _bootstrap_control_plane() -> None:
    """Check the published metadata epoch and prepare the managed payload bucket."""
    await init_nova_system()
    from app.modules.workspaces.storage_bootstrap import ensure_workspace_bucket

    await asyncio.to_thread(ensure_workspace_bucket)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Fail fast on missing/placeholder signing and encryption keys (NOVA-108).
    # This runs before any connection is opened so a misconfigured deployment
    # dies at boot with a clear message instead of at first login.
    require_configured_secrets()
    log_studio_capabilities(logger, "api")
    role = settings.NOVA_PROCESS_ROLE
    service = _service_label(role)

    # Control metadata lives in nova_control. Admit this process only at the
    # published schema and epoch, with its own workload login; there is no
    # fallback to NOVA_SYSTEM metadata.
    metadata = await open_process_metadata(role)
    app.state.metadata = metadata
    try:
        async with _serve(app, role, service):
            yield
    finally:
        app.state.metadata = None
        await close_process_metadata(metadata, service)


@asynccontextmanager
async def _serve(app: FastAPI, role: str, service: str):
    """Everything the API process runs once its metadata pool is installed."""
    await db.init_system_pool()
    from app.sql_frontend.capabilities.starrocks import resolve_engine_capabilities

    await resolve_engine_capabilities()
    # Align the engine's global time_zone with Nova's session pin (advisory; the
    # per-session init_command is the guarantee). Best-effort inside the method.
    await db.apply_global_time_zone()
    await session_store.init()
    control = _owns_control_plane(role)
    if control:
        await _bootstrap_exclusively()

    # MySQL protocol proxy. Embedded only in the single-process role, where the
    # web service alone is enough to serve port 4406; a split deployment runs
    # `python -m app.proxy`, the same server, as its own process. A proxy that
    # cannot bind (port already taken by a standalone proxy, most likely) must
    # not take the web service down with it.
    proxy_server = None
    embed_proxy = settings.PROXY_ENABLED and role == "all"
    PROXY_EXPECTED.set(1 if embed_proxy else 0)
    if embed_proxy:
        try:
            from app.proxy.server import MySQLProxyServer

            proxy_server = MySQLProxyServer()
            await proxy_server.start()
        except Exception as e:
            logger.warning("MySQL proxy did not start: %s", e)
            proxy_server = None

    # Telemetry is buffered per process, so every role flushes its own.
    from app.modules.query_autopilot.repository import repository as autopilot_repository
    from app.modules.query_autopilot.telemetry import collector as autopilot_collector

    autopilot_stop = asyncio.Event()
    autopilot_flush = asyncio.create_task(
        autopilot_collector.run(autopilot_stop, autopilot_repository)
    )
    # The same holds for AI function usage: whichever process the engine calls
    # records what it forwarded.
    llm_usage_flush = asyncio.create_task(llm_gateway.meter.run(autopilot_stop))
    SERVICE_UP.labels(service=service).set(1)
    app.state.started = True
    try:
        yield
    finally:
        app.state.started = False
        SERVICE_UP.labels(service=service).set(0)
    # Shutdown
    if proxy_server is not None:
        try:
            await proxy_server.stop()
        except Exception as e:
            logger.warning("MySQL proxy did not stop cleanly: %s", e)
    autopilot_stop.set()
    await autopilot_flush
    await llm_usage_flush
    await session_store.close()
    await db.close_system_pool()


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title="Nova",
        version="0.1.0",
        description=(
            "Enterprise Intelligence OS for governed data, AI agents, "
            "decisions, actions, and learning."
        ),
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
    app.add_middleware(HTTPMetricsMiddleware)

    # Exception handlers
    register_exception_handlers(app)

    # API v1 routers
    prefix = "/api/v1"
    app.include_router(auth_router, prefix=f"{prefix}/auth", tags=["auth"])
    app.include_router(query_router, prefix=f"{prefix}/query", tags=["query"])
    app.include_router(
        query_autopilot_router, prefix="/api/v1/query-autopilot", tags=["query-autopilot"]
    )
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
    app.include_router(search_router, prefix=f"{prefix}/ai/search", tags=["ai-search"])
    app.include_router(
        semantic_views_router,
        prefix=f"{prefix}/semantic-views",
        tags=["semantic-views"],
    )
    app.include_router(
        semantic_autopilot_router, prefix=f"{prefix}/semantic-views", tags=["semantic-views"]
    )
    app.include_router(entities_router, prefix=f"{prefix}/entities", tags=["entities"])
    app.include_router(feature_store_router, prefix=f"{prefix}/features", tags=["features"])
    app.include_router(llm_fn_router, prefix=f"{prefix}/ai", tags=["ai"])
    app.include_router(ml_router, prefix=f"{prefix}/ml", tags=["ml"])
    app.include_router(ml_internal_router, prefix=f"{prefix}/internal/ml", tags=["internal"])
    # Called by the engine's ``ai_query()``, not by browsers. Authenticated by
    # the per-alias token in the function body; keep it off the public gateway.
    app.include_router(llm_gateway_router, prefix=f"{prefix}/internal/llm", tags=["internal"])
    app.include_router(superset_router, prefix=f"{prefix}/superset", tags=["superset"])
    # Called only by Superset's services; never routed through the gateway.
    app.include_router(
        superset_internal_router, prefix=f"{prefix}/internal/superset", tags=["internal"]
    )
    app.include_router(workspaces_router, prefix=f"{prefix}/workspaces", tags=["workspaces"])
    app.include_router(notebooks_router, prefix=f"{prefix}/notebooks", tags=["notebooks"])
    app.include_router(monitoring_router, prefix=f"{prefix}/monitoring", tags=["monitoring"])
    app.include_router(functions_router, prefix=f"{prefix}/functions", tags=["functions"])
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
    app.include_router(streams_router, prefix=f"{prefix}/streams", tags=["streams"])
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
    from app.modules.agents.mission_router import router as mission_router
    from app.modules.agents.quality_router import router as quality_router
    from app.modules.assistant.analysis_workspace import router as analysis_workspace_router
    from app.modules.intelligence.action_router import router as action_router

    app.include_router(mission_router, prefix=f"{prefix}/agents", tags=["studio-missions"])
    app.include_router(
        analysis_workspace_router, prefix=f"{prefix}/agents", tags=["analytical-workspace"]
    )
    app.include_router(
        action_router, prefix=f"{prefix}/intelligence", tags=["intelligence-actions"]
    )

    app.include_router(quality_router, prefix=f"{prefix}/agents", tags=["agent-quality"])
    # Studio's one path from a result to Dataviz (docs/arch-19-studio-dataviz.md).
    app.include_router(dataviz_router, prefix=f"{prefix}/agents/studio/dataviz", tags=["dataviz"])
    app.include_router(studio_router, prefix=f"{prefix}/agents", tags=["agents"])
    app.include_router(agents_router, prefix=f"{prefix}/agents", tags=["agents"])
    app.include_router(knowledge_router, prefix=f"{prefix}/agents", tags=["agent-knowledge"])
    app.include_router(intelligence_engine_router, prefix=f"{prefix}/intelligence",
                       tags=["intelligence"])
    app.include_router(ontology_router, prefix=f"{prefix}/ontology", tags=["ontology"])
    app.include_router(
        business_events_router, prefix=f"{prefix}/intelligence", tags=["business-events"]
    )
    # Nova Catalog: catalog store, sync, and catalog management.
    app.include_router(catalog_router, prefix=f"{prefix}/catalog", tags=["catalog"])
    app.include_router(
        catalog_connections_router, prefix=f"{prefix}/catalog", tags=["catalog"]
    )
    # Nova Catalog business metadata, search, and lineage on securable ids.
    app.include_router(catalog_metadata_router, prefix=f"{prefix}/catalog", tags=["catalog"])
    # External catalogs (Iceberg + Hive, GA-only), an alias of Nova Catalog.
    app.include_router(
        external_catalogs_router,
        prefix=f"{prefix}/external-catalogs",
        tags=["external-catalogs"],
    )
    # Phase 11 — Migration Connector: assess, dry-run, plan, and execute.
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
        return {"status": "ok", "version": "0.1.0", "role": settings.NOVA_PROCESS_ROLE}

    @app.get("/ready")
    async def ready():
        """Whether this process can serve a request now.

        ``/health`` only says the process is up. This also requires startup to
        have finished and the engine and Redis to answer, which is what a
        gateway or orchestrator needs before sending traffic.
        """
        metadata = getattr(app.state, "metadata", None)
        if not getattr(app.state, "started", False) or metadata is None:
            return JSONResponse(status_code=503, content={"status": "starting"})
        role = settings.NOVA_PROCESS_ROLE
        try:
            async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
                # ``read_only`` while the deployment is frozen: reads still
                # serve, metadata writes are refused with 503.
                metadata_state = await check_metadata(metadata, _service_label(role))
                await db.execute_system("SELECT 1")
                await session_store._redis.ping()
        except Exception as exc:
            logger.warning("Readiness check failed: %s", type(exc).__name__)
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready", "role": role, "metadata": metadata_state}

    app.add_api_route("/metrics", metrics_response, methods=["GET"], include_in_schema=False)

    return app


app = create_app()
