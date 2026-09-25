"""Bounded application state supplied by a Nova surface for one Nove turn."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.common.sql_guard import CredentialsRedactionError, redact_sql_credentials

MAX_APP_CONTEXT_BYTES = 24_576
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_SENSITIVE_TEXT = re.compile(
    r"password|passwd|passphrase|secret|token|credential|"
    r"api[\s._-]*key|access[\s._-]*key|private[\s._-]*key|"
    r"account[\s._-]*key|authorization|authentication|bearer|"
    r"identified\s+by|cookie",
    re.IGNORECASE,
)
_URL_USERINFO = re.compile(r"://[^\s/@]+:[^\s/@]+@")


def _contains_sensitive_text(value: str) -> bool:
    from app.modules.assistant.tools.redaction import is_credential_value

    return bool(
        _SENSITIVE_TEXT.search(value)
        or _URL_USERINFO.search(value)
        or is_credential_value(value)
    )


def _safe_text(value: str) -> str:
    if _contains_sensitive_text(value):
        return "***"
    try:
        redacted = redact_sql_credentials(value)
    except CredentialsRedactionError:
        return "***"
    return "***" if redacted != value else value


def _safe_metadata(value: Any, depth: int = 0, *, sql_text: bool = False) -> Any:
    from app.modules.assistant.tools.redaction import is_credential_column

    if depth > 4:
        return None
    if isinstance(value, dict):
        return {
            str(key)[:80]: _safe_metadata(
                item, depth + 1, sql_text=str(key).casefold() == "sql"
            )
            for key, item in list(value.items())[:32]
            if not is_credential_column(str(key))
            and not _contains_sensitive_text(str(key))
        }
    if isinstance(value, list):
        return [_safe_metadata(item, depth + 1) for item in value[:24]]
    if isinstance(value, str):
        return _safe_text(value)[:4000 if sql_text else 2048]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return None


class _ContextPart(BaseModel):
    model_config = {"extra": "ignore", "populate_by_name": True}

    @model_validator(mode="after")
    def scrub_free_form_strings(self) -> _ContextPart:
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if isinstance(value, str):
                setattr(self, field_name, _safe_text(value))
        return self


class AppSurface(_ContextPart):
    id: str = Field(min_length=1, max_length=128)
    route: str = Field(min_length=1, max_length=512)
    title: str | None = Field(default=None, max_length=256)
    version: int | None = Field(default=None, ge=0)


class AppEntity(_ContextPart):
    type: str = Field(min_length=1, max_length=80)
    id: str | None = Field(default=None, max_length=256)
    name: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def scrub_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_metadata(value)


class AppSelection(_ContextPart):
    type: str | None = Field(default=None, max_length=80)
    ids: list[str] = Field(default_factory=list, max_length=32)
    text: str | None = Field(default=None, max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ids")
    @classmethod
    def check_ids(cls, values: list[str]) -> list[str]:
        if any(len(value) > 256 for value in values):
            raise ValueError("Selection ids must be at most 256 characters")
        return [_safe_text(value) for value in values]

    @field_validator("text")
    @classmethod
    def scrub_text(cls, value: str | None) -> str | None:
        return _safe_text(value) if value is not None else None

    @field_validator("metadata")
    @classmethod
    def scrub_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_metadata(value)


class AppCursor(_ContextPart):
    line: int | None = Field(default=None, ge=0)
    column: int | None = Field(default=None, ge=0)


class AppEditor(_ContextPart):
    document_id: str | None = Field(default=None, alias="documentId", max_length=256)
    language: str | None = Field(default=None, max_length=40)
    selected_text: str | None = Field(default=None, alias="selectedText", max_length=4096)
    cursor: AppCursor | None = None
    dirty: bool | None = None

    @field_validator("selected_text")
    @classmethod
    def scrub_selected_text(cls, value: str | None) -> str | None:
        return _safe_text(value) if value is not None else None


class AppResultColumn(_ContextPart):
    name: str = Field(min_length=1, max_length=128)
    type: str | None = Field(default=None, max_length=80)


class AppExecution(_ContextPart):
    type: str | None = Field(default=None, max_length=80)
    execution_id: str | None = Field(default=None, alias="executionId", max_length=128)
    status: Literal["idle", "running", "success", "error"] | None = None
    error_code: str | None = Field(default=None, alias="errorCode", max_length=128)
    error_message: str | None = Field(default=None, alias="errorMessage", max_length=2048)
    elapsed_ms: float | None = Field(default=None, alias="elapsedMs", ge=0)
    row_count: int | None = Field(default=None, alias="rowCount", ge=0)
    result_schema: list[AppResultColumn] = Field(
        default_factory=list, alias="resultSchema", max_length=64
    )

    @field_validator("error_message")
    @classmethod
    def scrub_error_message(cls, value: str | None) -> str | None:
        return _safe_text(value) if value is not None else None


class AppView(_ContextPart):
    active_tab: str | None = Field(default=None, alias="activeTab", max_length=128)
    filters: dict[str, Any] = Field(default_factory=dict)
    search: str | None = Field(default=None, max_length=256)
    sort: Any = None

    @field_validator("filters", "sort")
    @classmethod
    def scrub_values(cls, value: Any) -> Any:
        return _safe_metadata(value)


class AppDomain(_ContextPart):
    database: str | None = Field(default=None, max_length=128)
    schema_name: str | None = Field(default=None, alias="schema", max_length=128)
    role: str | None = Field(default=None, max_length=128)
    semantic_model: str | None = Field(default=None, alias="semanticModel", max_length=128)
    provider_id: str | None = Field(default=None, alias="providerId", max_length=128)


class NoveApplicationEvent(_ContextPart):
    id: str = Field(min_length=1, max_length=128)
    timestamp: datetime
    source: Literal["assistant", "surface", "execution", "user"]
    type: str = Field(min_length=1, max_length=80)
    surface_id: str | None = Field(default=None, alias="surfaceId", max_length=128)
    correlation_id: str | None = Field(default=None, alias="correlationId", max_length=128)
    artifact_id: str | None = Field(default=None, alias="artifactId", max_length=128)
    execution_id: str | None = Field(default=None, alias="executionId", max_length=128)
    status: Literal["success", "failure"] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def scrub_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_metadata(value)


class NoveAppContext(_ContextPart):
    version: Literal[1]
    surface: AppSurface
    entity: AppEntity | None = None
    selection: AppSelection | None = None
    editor: AppEditor | None = None
    execution: AppExecution | None = None
    view: AppView | None = None
    domain: AppDomain | None = None
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    events: list[NoveApplicationEvent] = Field(default_factory=list, max_length=12)

    @model_validator(mode="before")
    @classmethod
    def bound_raw_envelope(cls, value: Any) -> Any:
        try:
            size = len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Application context must be serializable") from exc
        if size > MAX_APP_CONTEXT_BYTES:
            raise ValueError("Application context exceeds 24 KB")
        return value

    @field_validator("capabilities")
    @classmethod
    def check_capabilities(cls, values: list[str]) -> list[str]:
        values = [value for value in values if not _contains_sensitive_text(value)]
        if any(not _CAPABILITY_NAME.fullmatch(value) for value in values):
            raise ValueError("Application context has an invalid capability name")
        return list(dict.fromkeys(values))

    def current_events(self) -> list[NoveApplicationEvent]:
        current_document = self.editor.document_id if self.editor else None
        current_entity = self.entity.id if self.entity else None
        visible: list[NoveApplicationEvent] = []
        for event in self.events:
            if event.surface_id is not None and event.surface_id != self.surface.id:
                continue
            document_id = event.payload.get("documentId")
            if document_id and document_id != current_document:
                continue
            entity_id = event.payload.get("entityId")
            if entity_id and entity_id != current_entity:
                continue
            visible.append(event)
        return visible[-8:]

    def prompt_data(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, mode="json", exclude_none=True)
        data["events"] = [
            event.model_dump(by_alias=True, mode="json", exclude_none=True)
            for event in self.current_events()
        ]
        return data


_DEICTIC = re.compile(
    r"\b(this|that|it|here|these|current|selected|last|ini|itu|terakhir)\b", re.I
)


def resolve_app_references(request: str, app: NoveAppContext | None) -> dict[str, Any]:
    """Give the model bounded references from the current surface, not old turns."""
    if app is None or not _DEICTIC.search(request):
        return {}
    result: dict[str, Any] = {"surfaceId": app.surface.id}
    selection = app.selection
    if selection and (selection.ids or selection.text):
        result["selection"] = selection.model_dump(by_alias=True, exclude_none=True)
    if app.entity is not None:
        result["entity"] = app.entity.model_dump(by_alias=True, exclude_none=True)
    if app.execution is not None and app.execution.status in {"error", "success"}:
        result["execution"] = app.execution.model_dump(by_alias=True, exclude_none=True)
    elif failure := next(
        (event for event in reversed(app.current_events()) if event.type == "query_failed"),
        None,
    ):
        result["lastFailure"] = failure.model_dump(by_alias=True, exclude_none=True)
    if app.editor is not None and app.editor.selected_text:
        result["editorSelection"] = app.editor.selected_text
    return result
