from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.sql_frontend.ast.statements import Statement


@dataclass(slots=True)
class PlanningContext:
    database: str | None = None
    schema: str | None = None
    binder: Any = None
    relation_binder: Any = None
    capabilities: Any = None
    semantics: Any = None
    ranger_enabled: bool = False
    confirm_destructive: bool = False
    validated: dict[int, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class ExecutionContext:
    username: str
    database: str | None = None
    schema: str | None = None
    role: str | None = None
    max_rows: int | None = None
    session_id: str | None = None
    file_id: str | None = None
    tenant: str = "default"
    security_context_version: int = 1
    confirm_destructive: bool = False
    allow_stage_export: bool = False
    encrypted_password: str = field(default="", repr=False)
    connection: Any = field(default=None, repr=False)
    capabilities: Any = None
    engine_session_prepared: bool = False
    transaction_active: bool = False
    binder: Any = field(default=None, repr=False)
    statements: dict[int, Statement] = field(default_factory=dict, repr=False)
    validated: dict[int, Any] = field(default_factory=dict, repr=False)
