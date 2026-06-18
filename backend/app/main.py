"""Nova Backend — FastAPI App Factory.

StarRocks management console backend with domain-driven modular architecture.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.common.nova_system import init_nova_system
from app.core.config import settings
from app.core.database import db
from app.core.exceptions import register_exception_handlers
from app.core.redis import session_store

# --- Module routers ---
from app.modules.auth.router import router as auth_router
from app.modules.query.router import router as query_router

# Future modules (uncomment as implemented):
# from app.modules.query.router import router as query_router
# from app.modules.objects.router import router as objects_router
# from app.modules.stages.router import router as stages_router
# from app.modules.users.router import router as users_router
# from app.modules.tables.router import router as tables_router
# from app.modules.views.router import router as views_router
# from app.modules.functions.router import router as functions_router
# from app.modules.tasks.router import router as tasks_router
# from app.modules.pipes.router import router as pipes_router
# from app.modules.external_catalogs.router import router as ext_router
# from app.modules.cluster.router import router as cluster_router
# from app.modules.resource_groups.router import router as rg_router
# from app.modules.ai_ml.router import router as ai_router
# from app.modules.dashboards.router import router as dash_router
# from app.modules.backup.router import router as backup_router
# from app.modules.governance.router import router as gov_router
# from app.modules.variables.router import router as var_router
# from app.modules.system.router import router as sys_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Startup
    await db.init_system_pool()
    await session_store.init()
    await init_nova_system()
    yield
    # Shutdown
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
    )

    # Exception handlers
    register_exception_handlers(app)

    # API v1 routers
    prefix = "/api/v1"
    app.include_router(auth_router, prefix=f"{prefix}/auth", tags=["auth"])
    app.include_router(query_router, prefix=f"{prefix}/query", tags=["query"])

    # Future modules:
    # app.include_router(query_router, prefix=f"{prefix}/query", tags=["query"])
    # app.include_router(objects_router, prefix=f"{prefix}/objects", tags=["objects"])
    # app.include_router(stages_router, prefix=f"{prefix}/stages", tags=["stages"])
    # app.include_router(users_router, prefix=f"{prefix}/users", tags=["users"])
    # ... etc

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    return app


app = create_app()
