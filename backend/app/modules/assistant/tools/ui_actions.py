"""Bounded access to the same JSON API operations used by the Nova UI."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.common.sql_guard import (
    CredentialsRedactionError,
    redact_sql_credentials,
    split_sql_statements,
)
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.assistant.tools.redaction import (
    is_credential_column,
    is_credential_value,
    redact_rows,
)

_MAX_INPUT_CHARS = 16_000
_MAX_RESPONSE_CHARS = 8_000
_MAX_SEARCH_RESULTS = 8
_PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")
_SECRET_QUERY_KEYS = frozenset({"token", "key", "secret", "credential", "signature"})
_BLOCKED_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/internal/",
)
_BLOCKED_SUFFIXES = (
    "/reset-password",
    "/decision",
    "/grant",
    "/messages",
)
_BINARY_GET_PATHS = frozenset({"/api/v1/stages/{stage_id}/files/{filename}"})
_FILE_UPLOAD_PATHS = frozenset({
    "/api/v1/stages/{stage_id}/files",
    "/api/v1/explorer/databases/{database}/stages/{stage}/files",
})
_THREAD_DETAIL_PATHS = frozenset({
    "/api/v1/assistant/threads/{thread_id}",
    "/api/v1/agents/{agent_id}/threads/{thread_id}",
})
_SEARCH_ALIASES = {
    "user": "users pengguna akun create user",
    "role": "roles peran grant privilege member maintenance",
    "scope": "access control data scopes ranger row filter role",
    "workspace": "workspaces files worksheet sql editor folder",
    "mcp": "agents mcp servers connectors discover tools",
    "semantic": "agents semantic models views datasets metrics",
    "agent": "agents studio create configure tools",
    "sql": "query execute sql worksheet statement starrocks",
}
_METHOD_HINTS = {
    "GET": {"list", "show", "read", "lihat", "tampilkan", "cari"},
    "POST": {"create", "add", "buat", "tambah", "connect", "hubungkan"},
    "PUT": {"edit", "update", "ubah", "simpan", "replace"},
    "PATCH": {"edit", "update", "ubah", "simpan", "configure"},
    "DELETE": {"delete", "remove", "hapus", "revoke", "cabut"},
}
_ACTION_METHODS = {
    "create": "POST", "add": "POST", "connect": "POST", "upload": "POST",
    "grant": "POST", "assign": "POST", "buat": "POST", "buatkan": "POST",
    "tambah": "POST", "tambahkan": "POST", "hubungkan": "POST",
    "unggah": "POST", "berikan": "POST",
    "run": "POST", "execute": "POST", "jalankan": "POST", "eksekusi": "POST",
    "edit": "PUT", "update": "PUT", "ubah": "PUT", "simpan": "PUT", "ganti": "PUT",
    "rename": "PUT", "delete": "DELETE", "remove": "DELETE", "hapus": "DELETE",
    "cabut": "DELETE",
}
_SEARCH_SYNONYMS = {
    "add": "create", "connect": "create", "buat": "create", "buatkan": "create",
    "tambah": "create", "tambahkan": "create", "hubungkan": "create",
    "edit": "update", "ubah": "update", "simpan": "update", "ganti": "update",
    "unggah": "upload", "hapus": "delete", "cabut": "delete",
    "view": "model", "role": "roles", "scope": "scopes",
    "server": "servers", "agent": "agents", "user": "users",
}
_SEARCH_STOPWORDS = {"a", "an", "the", "to", "for", "in", "on", "di", "ke", "untuk"}


def _search_word(word: str) -> str:
    word = _SEARCH_SYNONYMS.get(word, word)
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


@dataclass(frozen=True)
class UIOperation:
    key: str
    method: str
    path: str
    summary: str
    tags: tuple[str, ...]
    parameters: tuple[dict[str, Any], ...]
    body_schema: dict[str, Any] | None


def _expand_schema(value: Any, components: dict[str, Any], depth: int = 0) -> Any:
    if depth >= 8:
        return value
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str):
            target = components.get(reference.split("/")[-1])
            if isinstance(target, dict):
                return _expand_schema(target, components, depth + 1)
        return {
            key: _expand_schema(item, components, depth + 1)
            for key, item in value.items()
            if key not in {"title", "examples"}
        }
    if isinstance(value, list):
        return [_expand_schema(item, components, depth + 1) for item in value]
    return value


@lru_cache(maxsize=1)
def _catalog() -> dict[str, UIOperation]:
    from app.main import app

    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    operations: dict[str, UIOperation] = {}
    for path, path_spec in spec.get("paths", {}).items():
        if not path.startswith("/api/v1/") or any(
            path.startswith(prefix) for prefix in _BLOCKED_PREFIXES
        ):
            continue
        if any(path.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
            continue
        if "/tool-calls/" in path:
            continue
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            operation_spec = path_spec.get(method.lower(), {})
            if not operation_spec:
                continue
            if method == "GET" and path in _BINARY_GET_PATHS | _THREAD_DETAIL_PATHS:
                continue
            request_body = operation_spec.get("requestBody", {}).get("content", {})
            file_upload = method == "POST" and path in _FILE_UPLOAD_PATHS
            if request_body and "application/json" not in request_body and not file_upload:
                continue
            schema = request_body.get("application/json", {}).get("schema")
            if isinstance(schema, dict):
                schema = _expand_schema(schema, components)
            if file_upload:
                schema = {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        **(
                            {"filename": {"type": "string"}}
                            if path.startswith("/api/v1/explorer/") else {}
                        ),
                    },
                    "required": ["file"],
                }
            key = f"{method} {path}"
            operations[key] = UIOperation(
                key=key,
                method=method,
                path=path,
                summary=operation_spec.get("summary") or operation_spec.get("operationId", key),
                tags=tuple(operation_spec.get("tags") or ()),
                parameters=tuple(operation_spec.get("parameters") or ()),
                body_schema=schema if isinstance(schema, dict) else None,
            )
    return operations


def _is_sensitive_key(key: str) -> bool:
    return is_credential_column(key) or key.casefold() in {
        "authorization", "cookie", "set-cookie", "private_key", "endpoint_auth"
    }


def _has_sensitive_input(value: Any, *, key: str = "") -> bool:
    if _is_sensitive_key(key):
        return value is not None
    if isinstance(value, dict):
        return any(_has_sensitive_input(item, key=str(name)) for name, item in value.items())
    if isinstance(value, list):
        return any(_has_sensitive_input(item, key=key) for item in value)
    if isinstance(value, str):
        if is_credential_value(value):
            return True
        if key.casefold() in {"command", "args"} and re.search(
            r"(?i)(?:--?(?:password|passwd|secret|token|api[-_]?key|credential)|"
            r"[A-Z_]*(?:SECRET|TOKEN|PASSWORD|API_KEY))\s*(?:=|\s|$)",
            value,
        ):
            return True
        if key.casefold() in {"sql", "content", "definition"}:
            try:
                if redact_sql_credentials(value) != value:
                    return True
            except CredentialsRedactionError:
                return True
            if re.search(r"\bIDENTIFIED\s+BY\b", value, re.IGNORECASE):
                return True
            if re.search(r"\b(?:CREATE|ALTER)\s+USER\b|\bSET\s+PASSWORD\b", value, re.IGNORECASE):
                return True
        if key.casefold() in {"url", "endpoint", "uri"}:
            parsed = urlsplit(value)
            if parsed.username or parsed.password:
                return True
            if parsed.query and any(
                any(part in name.casefold() for part in _SECRET_QUERY_KEYS)
                for name in (pair.split("=", 1)[0] for pair in parsed.query.split("&"))
            ):
                return True
    return False


def _safe_value(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_key(key):
        return "***"
    if isinstance(value, dict):
        if isinstance(value.get("columns"), list) and isinstance(value.get("rows"), list):
            value = {
                **value,
                "rows": redact_rows(
                    [str(column) for column in value["columns"]],
                    [row for row in value["rows"] if isinstance(row, list)],
                ),
            }
        return {str(name): _safe_value(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        if key.casefold() == "args" and _has_sensitive_input(value, key=key):
            return ["***"] * len(value)
        return [_safe_value(item, key=key) for item in value]
    if isinstance(value, str):
        if _has_sensitive_input(value, key=key):
            return "***"
        return SanitizingJSONResponse._sanitize(value)
    return value


def _resolved_path(operation: UIOperation, path_params: dict[str, Any]) -> str:
    required = set(_PATH_PARAMETER.findall(operation.path))
    if set(path_params) != required:
        raise ValueError(f"Path parameters required: {', '.join(sorted(required)) or 'none'}")
    path = operation.path
    for name in required:
        value = path_params[name]
        stage_filename = (
            name == "filename"
            and operation.path.startswith("/api/v1/stages/{stage_id}/files/{filename}")
        )
        if (
            not isinstance(value, str | int)
            or not str(value)
            or str(value) in {".", ".."}
            or any(character in str(value) for character in "\\%?#")
            or ("/" in str(value) and not stage_filename)
        ):
            raise ValueError(f"Invalid path parameter: {name}")
        if stage_filename:
            from app.modules.stages.service import stage_service

            try:
                if stage_service._safe_relative_path(str(value)) != str(value):
                    raise ValueError("Path must be normalized")
            except ValueError as exc:
                raise ValueError(f"Invalid path parameter: {name}") from exc
        path = path.replace("{" + name + "}", quote(str(value), safe="/" if stage_filename else ""))
    return path


def _request_parts(invocation: ToolInvocation) -> tuple[UIOperation, str, dict, dict, Any]:
    args = invocation.arguments
    key = args.get("operation")
    operation = _catalog().get(key) if isinstance(key, str) else None
    if operation is None:
        raise ValueError("Unknown or unavailable UI operation. Search for an operation first.")
    path_params = args.get("path_params") or {}
    query = args.get("query") or {}
    body = args.get("body")
    if not isinstance(path_params, dict) or not isinstance(query, dict):
        raise ValueError("Path parameters and query must be objects.")
    if body is not None and not isinstance(body, dict | list):
        raise ValueError("The request body must be a JSON object or array.")
    if operation.method in {"GET", "DELETE"} and body is not None and operation.body_schema is None:
        raise ValueError("This operation does not accept a request body.")
    if operation.path in _FILE_UPLOAD_PATHS and body is not None:
        if operation.path.startswith("/api/v1/explorer/") and (
            isinstance(body, dict)
            and set(body) == {"filename"}
            and isinstance(body["filename"], str)
        ):
            from app.modules.stages.service import stage_service

            try:
                if stage_service._safe_relative_path(body["filename"]) != body["filename"]:
                    raise ValueError("Filename must be normalized")
            except ValueError as exc:
                raise ValueError("Invalid destination filename") from exc
        else:
            raise ValueError("Choose the stage file in the approval card, not in tool arguments.")
    if operation.path == "/api/v1/query/execute":
        if not isinstance(body, dict) or not isinstance(body.get("sql"), str):
            raise ValueError("SQL execution requires a SQL statement.")
        if len(split_sql_statements(body["sql"])) != 1:
            raise ValueError("Approve one SQL statement at a time.")
        from app.proxy.session import parse_role_statement

        if parse_role_statement(body["sql"]) is not None:
            raise ValueError("Use query_execute to switch the active role.")
        body = {**body, "confirm_destructive": True, "max_rows": 100}
    if any(_has_sensitive_input(value) for value in (path_params, query, body)):
        raise ValueError("Credential-bearing values cannot be passed through Nove tools.")
    serialized = json.dumps({"path_params": path_params, "query": query, "body": body})
    if len(serialized) > _MAX_INPUT_CHARS:
        raise ValueError("This action is too large for the approval card.")
    path = _resolved_path(operation, path_params)
    return operation, path, path_params, query, body


class FindUIOperationTool:
    name = "find_ui_operation"
    description = (
        "Find the exact Nova UI API operation for a requested Nova action. "
        "Use before call_ui_operation for workspace, user, role, scope, agent, "
        "semantic model, MCP connector, and other Nova UI tasks."
    )
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        return "Find Nova UI operations"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        query = str(invocation.arguments.get("query") or "").strip()[:200]
        if not query:
            return ToolOutcome(ok=False, summary="", error="A search query is required.")
        query_words = re.findall(r"[a-z0-9]+", query.casefold())
        original = set(query_words)
        terms = original | {
            replacement for term, replacement in _SEARCH_SYNONYMS.items() if term in original
        }
        expanded = " ".join(
            alias for name, alias in _SEARCH_ALIASES.items() if name in terms
        )
        secondary = set(re.findall(r"[a-z0-9]+", expanded.casefold()))
        requested_methods = {
            method for term, method in _ACTION_METHODS.items() if term in original
        }
        if "PUT" in requested_methods:
            requested_methods.add("PATCH")
        if "rename" in original:
            requested_methods.add("POST")
        primary = next(
            (
                _search_word(word) for word in query_words
                if word not in _ACTION_METHODS and word not in _SEARCH_STOPWORDS
            ),
            None,
        )
        query_core = {_search_word(word) for word in original - _SEARCH_STOPWORDS}
        ranked: list[tuple[int, UIOperation]] = []
        for operation in _catalog().values():
            if requested_methods and operation.method not in requested_methods:
                continue
            summary_words = set(re.findall(r"[a-z0-9]+", operation.summary.casefold()))
            path_words = set(re.findall(r"[a-z0-9]+", operation.path.casefold()))
            if "upload" in original and "upload" not in summary_words:
                continue
            if "upload" in original and not (
                (original - {"upload"} - _SEARCH_STOPWORDS) & (summary_words | path_words)
            ):
                continue
            score = 12 * len(terms & summary_words) + 5 * len(terms & path_words)
            score += len(secondary & (summary_words | path_words))
            if terms & _METHOD_HINTS[operation.method]:
                score += 3
            summary_core = {_search_word(word) for word in summary_words}
            if summary_core == query_core:
                score += 25
            if primary and primary in summary_core:
                score += 12
            if "sql" in original and "workspace" in original and "/files" in operation.path:
                score += 8
            if (
                "sql" in original
                and {"run", "execute", "jalankan", "eksekusi"} & original
                and operation.path == "/api/v1/query/execute"
            ):
                score += 40
            score -= operation.path.count("{") * 2
            if score > 0:
                ranked.append((score, operation))
        ranked.sort(key=lambda item: (-item[0], item[1].key))
        matches = []
        for _, operation in ranked[:_MAX_SEARCH_RESULTS]:
            schema = operation.body_schema or {}
            matches.append({
                "operation": operation.key,
                "summary": operation.summary,
                "path_parameters": [
                    p.get("name") for p in operation.parameters if p.get("in") == "path"
                ],
                "query_parameters": [
                    p.get("name") for p in operation.parameters if p.get("in") == "query"
                ],
                "body_properties": list((schema.get("properties") or {}).keys()),
                "body_required": schema.get("required") or [],
                "secure_input_required": any(
                    _is_sensitive_key(field) for field in schema.get("required") or []
                ),
                "body_schema": (
                    schema
                    if len(matches) == 0 and len(json.dumps(schema)) < 4_000
                    else None
                ),
                "approval_required": operation.method != "GET",
            })
        return ToolOutcome(
            ok=True,
            summary=f"Found {len(matches)} matching Nova UI operations.",
            data={"operations": matches},
        )


class CallUIOperationTool:
    name = "call_ui_operation"
    description = (
        "Execute one exact Nova UI API operation discovered by find_ui_operation, "
        "using the current user's authenticated session and normal API gates. "
        "Mutations require explicit approval. Never pass credentials."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string"},
            "path_params": {"type": "object", "additionalProperties": True},
            "query": {"type": "object", "additionalProperties": True},
            "body": {"type": "object", "additionalProperties": True},
        },
        "required": ["operation"],
        "additionalProperties": False,
    }
    requires_consent = True

    @property
    def classification(self) -> ToolClassification:
        return "destructive"

    def classification_for(self, invocation: ToolInvocation) -> ToolClassification:
        key = invocation.arguments.get("operation")
        operation = _catalog().get(key) if isinstance(key, str) else None
        return "read_only" if operation and operation.method == "GET" else "destructive"

    def preview(self, invocation: ToolInvocation) -> str:
        try:
            operation, path, _, query, body = _request_parts(invocation)
        except ValueError as exc:
            return f"Invalid Nova UI action: {exc}"
        payload = {"query": _safe_value(query), "body": _safe_value(body)}
        if operation.path in _FILE_UPLOAD_PATHS:
            payload["file"] = "Choose a local file in this approval card"
        return f"{operation.method} {path}\n" + json.dumps(payload, ensure_ascii=False, indent=2)

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        try:
            operation, path, _, query, body = _request_parts(invocation)
        except ValueError as exc:
            return ToolOutcome(ok=False, summary="", error=str(exc), error_class="INVALID_ACTION")
        user = getattr(context, "user", None) or {}
        username = user.get("username")
        session_id = user.get("session_id")
        if not username or not session_id:
            return ToolOutcome(
                ok=False, summary="", error="No authenticated Nova session is available."
            )
        if (
            operation.method == "DELETE"
            and path == f"/api/v1/assistant/threads/{getattr(context, 'thread_id', None)}"
        ):
            return ToolOutcome(
                ok=False, summary="", error="The active Nove conversation cannot delete itself."
            )

        secure_input = getattr(context, "secure_input", None)
        context.secure_input = None
        file_upload = getattr(context, "file_upload", None)
        context.file_upload = None
        if operation.key == "POST /api/v1/users":
            if (
                not isinstance(body, dict)
                or not secure_input
                or set(secure_input) != {"password"}
            ):
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error="A password is required in the secure approval form.",
                )
            body = {**body, "password": secure_input["password"]}
        elif secure_input:
            return ToolOutcome(
                ok=False, summary="", error="Secure input is not valid for this action."
            )
        if operation.path in _FILE_UPLOAD_PATHS:
            if not file_upload:
                return ToolOutcome(
                    ok=False, summary="", error="Choose a file in the approval card."
                )
        elif file_upload:
            file_upload[1].close()
            return ToolOutcome(
                ok=False, summary="", error="A file is not valid for this action."
            )
        sensitive_action = bool(secure_input or file_upload)

        from app.core.security import create_access_token
        from app.main import app

        try:
            if operation.method != "GET":
                await self._audit(context, operation, path, "PENDING")
            token = create_access_token(username, session_id)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://nova.internal",
                timeout=30.0,
            ) as client:
                request_kwargs = {
                    "params": query,
                    "headers": {"Authorization": f"Bearer {token}"},
                }
                if file_upload:
                    request_kwargs["files"] = {"file": file_upload}
                    if body is not None:
                        request_kwargs["data"] = body
                else:
                    request_kwargs["json"] = body
                response = await client.request(operation.method, path, **request_kwargs)
        except httpx.TimeoutException:
            await self._audit(context, operation, path, "UNKNOWN")
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    "The action timed out. Its final state is unknown; "
                    "verify it before retrying."
                ),
            )
        except httpx.HTTPError:
            await self._audit(context, operation, path, "FAILED")
            return ToolOutcome(ok=False, summary="", error="The Nova operation could not complete.")
        finally:
            if file_upload:
                file_upload[1].close()

        content_type = response.headers.get("content-type", "")
        if content_type and "application/json" not in content_type:
            audit_id = await self._audit(
                context, operation, path, "SUCCESS" if response.is_success else "FAILED"
            )
            return ToolOutcome(
                ok=response.is_success,
                summary=f"{operation.summary}: HTTP {response.status_code}.",
                data={"status_code": response.status_code},
                evidence={"audit_id": audit_id},
                error=None if response.is_success else "The Nova operation failed.",
            )
        try:
            payload = _safe_value(response.json()) if response.content else None
        except ValueError:
            payload = None
        failed_results = (
            [item for item in payload if isinstance(item, dict) and item.get("success") is False]
            if isinstance(payload, list)
            else []
        )
        result_failed = bool(failed_results) or (
            isinstance(payload, dict)
            and (payload.get("ok") is False or payload.get("success") is False)
        )
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        if len(encoded) > _MAX_RESPONSE_CHARS:
            payload = {"truncated": True, "preview": encoded[:_MAX_RESPONSE_CHARS]}
        audit_id = await self._audit(
            context,
            operation,
            path,
            "SUCCESS" if response.is_success and not result_failed else "FAILED",
        )
        if not response.is_success or result_failed:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if detail is None and isinstance(payload, dict):
                detail = payload.get("error")
            if failed_results:
                detail = failed_results[0].get("error") or "statement failed"
            if response.status_code >= 500:
                detail = "server error; verify the state before retrying"
            if sensitive_action:
                detail = "request rejected"
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    f"{operation.summary} failed (HTTP {response.status_code}): "
                    f"{str(detail or 'request rejected')[:500]}"
                ),
                error_class="UI_OPERATION_REJECTED",
            )
        completion = (
            "accepted; follow-up verification is needed"
            if response.status_code == 202
            else "completed"
        )
        return ToolOutcome(
            ok=True,
            summary=f"{operation.summary} {completion} (HTTP {response.status_code}).",
            data={"status_code": response.status_code, "result": payload},
            evidence={
                "operation": operation.key,
                "status_code": response.status_code,
                "audit_id": audit_id,
            },
        )

    @staticmethod
    async def _audit(context: Any, operation: UIOperation, path: str, status: str) -> str:
        user = getattr(context, "user", None) or {}
        return await write_audit_log(
            event_type="assistant_ui_action",
            user_name=user["username"],
            action=operation.method,
            object_type="API_OPERATION",
            object_name=path,
            status=status,
            session_id=getattr(context, "audit_session_id", None),
            active_role=user.get("active_role"),
            security_context_version=user.get("security_context_version"),
        )


find_ui_operation_tool = FindUIOperationTool()
call_ui_operation_tool = CallUIOperationTool()
