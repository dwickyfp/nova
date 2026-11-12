"""LLM function aliases and the rendering of their ``AI_*`` SQL functions.

Aliases live in the PostgreSQL metadata store (``nova_meta.config_model_aliases``)
next to the AI registry. Every alias change goes through
``AIConfigurationService``: the alias, the immutable function definition, its
registration job and the outbox wakeup commit in one transaction. The HTTP
process never drops or creates an engine function itself; the maintenance
participant in ``registration_worker`` applies each definition, verifies the
engine function and records a receipt.

Flow:
  1. An administrator creates or changes an alias (function_type → provider + model).
  2. The definition and its ``ai.function.reconcile`` job are stored atomically.
  3. The maintenance worker renders the ``AI_*`` function from that definition,
     applies it in StarRocks and verifies it before completing the job.
  4. SQL users call AI_COMPLETE(), AI_SENTIMENT(), ... which reach the gateway.
"""

import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import asyncmy
import asyncmy.cursors

from app.common.metadata_actors import metadata_scope
from app.core.config import settings
from app.modules.access_control.security_context import SecurityContext
from app.modules.ai_ml.metadata_service import AIConfigurationService
from app.modules.ai_ml.repository import AIRegistryRepository
from app.modules.ai_ml.service import REGISTRY_READER
from app.modules.llm_functions.gateway import (
    FORWARDED_PARAMETERS,
    alias_token,
    gateway_endpoint,
    llm_gateway,
)
from app.modules.llm_functions.registrations import FunctionRegistrationRepository
from app.modules.llm_functions.repository import AliasDefinition, ModelAliasRepository

logger = logging.getLogger(__name__)


# ── UDF Templates ─────────────────────────────────────────────
# Each template wraps ai_query() with a system prompt for the function type.
# The {config} placeholder is replaced with the resolved JSON config string.

UDF_TEMPLATES: dict[str, dict] = {
    "complete": {
        "function_name": "AI_COMPLETE",
        "params": ["prompt STRING"],
        "body": "ai_query(prompt, '{config}')",
        "system_prompt": "",
    },
    "sentiment": {
        "function_name": "AI_SENTIMENT",
        "params": ["txt STRING"],
        "body": (
            "ai_query(CONCAT('Analyze the sentiment of the following text. "
            'Reply with JSON: {"sentiment": '
            '"positive|negative|neutral|mixed", "confidence": '
            "0.0-1.0}\\n\\nText: ', txt), '{config}')"
        ),
        "system_prompt": (
            "Analyze the sentiment of the following text. Reply with JSON: "
            '{"sentiment": "positive|negative|neutral|mixed", "confidence": '
            "0.0-1.0}\n\nText: "
        ),
    },
    "classify": {
        "function_name": "AI_CLASSIFY",
        "params": ["txt STRING", "categories STRING"],
        "body": (
            "ai_query(CONCAT('Classify the following text into ONE of these "
            "categories: [', categories, ']. Reply with ONLY the category "
            "name, nothing else.\\n\\nText: ', txt), '{config}')"
        ),
        "system_prompt": (
            "Classify the following text into ONE of these categories: "
            "[{categories}]. Reply with ONLY the category name, nothing "
            "else.\n\nText: "
        ),
    },
    "summarize": {
        "function_name": "AI_SUMMARIZE",
        "params": ["txt STRING"],
        "body": (
            "ai_query(CONCAT('Summarize the following text concisely in 2-3 "
            "sentences:\\n\\n', txt), '{config}')"
        ),
        "system_prompt": "Summarize the following text concisely in 2-3 sentences:\n\n",
    },
    "extract": {
        "function_name": "AI_EXTRACT",
        "params": ["txt STRING", "json_schema STRING"],
        "body": (
            "ai_query(CONCAT('Extract structured information from the "
            "following text. Return as JSON matching this schema: ', "
            "json_schema, '. Reply with ONLY the JSON.\\n\\nText: ', txt), "
            "'{config}')"
        ),
        "system_prompt": (
            "Extract structured information from the following text. Return "
            "as JSON matching this schema: {json_schema}. Reply with ONLY the "
            "JSON.\n\nText: "
        ),
    },
    "translate": {
        "function_name": "AI_TRANSLATE",
        "params": ["txt STRING", "target_lang STRING"],
        "body": (
            "ai_query(CONCAT('Translate the following text to ', target_lang, "
            "'. Reply with ONLY the translation, no explanation.\\n\\nText: ', "
            "txt), '{config}')"
        ),
        "system_prompt": (
            "Translate the following text to {target_lang}. Reply with ONLY "
            "the translation, no explanation.\n\nText: "
        ),
    },
    "filter": {
        "function_name": "AI_FILTER",
        "params": ["txt STRING", "criteria STRING"],
        "body": (
            "ai_query(CONCAT('Does the following text match this criteria? "
            'Reply with ONLY "true" or "false".\\nCriteria: \', criteria, '
            "'\\n\\nText: ', txt), '{config}')"
        ),
        "system_prompt": (
            "Does the following text match this criteria? Reply with ONLY "
            '"true" or "false".\nCriteria: {criteria}\n\nText: '
        ),
    },
}


@dataclass(frozen=True)
class RenderedFunction:
    """The engine statements for one function definition. Never logged."""

    function_type: str
    function_name: str
    drop_sql: str = field(repr=False)
    create_sql: str = field(repr=False)
    body: str = field(repr=False)


def _placeholder_body(function_name: str, params: list[str]) -> str:
    param_names = [p.split()[0] for p in params]
    if not param_names:
        return f"'ERROR: {function_name} not configured.'"
    pairs: list[str] = []
    for name in param_names:
        pairs.extend((f"'{name}='", name, "', '"))
    return (
        f"CONCAT('ERROR: {function_name} not configured. "
        "Set up an alias in AI Providers > Functions tab. Input was: ', "
        f"{', '.join(pairs[:-1])})"
    )


def _body_with_prompt(function_type: str, system_prompt: str, config_str: str) -> str:
    """The ``ai_query`` body for an alias with its own system prompt."""
    escaped = system_prompt.replace("'", "''")
    if function_type in {"complete"}:
        return f"ai_query(CONCAT('{escaped}', prompt), '{config_str}')"
    if function_type in {"sentiment", "summarize"}:
        return f"ai_query(CONCAT('{escaped}', txt), '{config_str}')"
    if function_type == "classify":
        return f"ai_query(CONCAT('{escaped}', categories, ']: ', txt), '{config_str}')"
    if function_type == "extract":
        return (
            f"ai_query(CONCAT('{escaped}', json_schema, "
            f"'. Reply with ONLY the JSON.\\n\\nText: ', txt), '{config_str}')"
        )
    if function_type == "translate":
        return (
            f"ai_query(CONCAT('{escaped}', target_lang, "
            f"'. Reply with ONLY the translation.\\n\\nText: ', txt), '{config_str}')"
        )
    if function_type == "filter":
        return f"ai_query(CONCAT('{escaped}', criteria, '\\n\\nText: ', txt), '{config_str}')"
    params = [p.split()[0] for p in UDF_TEMPLATES[function_type]["params"]]
    return f"ai_query(CONCAT('{escaped}', {', '.join(params)}), '{config_str}')"


def render_function(snapshot: dict[str, Any]) -> RenderedFunction | None:
    """Render one immutable definition snapshot (contract version 1).

    ``None`` means Nova has no engine function for the type (``embed``, R33).
    A disabled or unconfigured definition renders the placeholder that fails
    with a clear message instead of reaching another provider.
    """
    if snapshot.get("contract_version") != 1:
        raise ValueError("Unsupported function definition contract")
    function_type = snapshot["function_type"]
    template = UDF_TEMPLATES.get(function_type)
    if template is None:
        return None
    name = template["function_name"]
    params_sql = ", ".join(template["params"])
    argument_types = ", ".join("STRING" for _ in template["params"])
    drop_sql = f"DROP GLOBAL FUNCTION IF EXISTS {name}({argument_types})"
    alias = snapshot.get("alias") or {}
    if snapshot.get("mode") != "configured":
        body = _placeholder_body(name, template["params"])
    else:
        model = snapshot.get("model") or {}
        model_name = model.get("name") or "gpt-4o-mini"
        # The body names only Nova's gateway and an alias token; the provider
        # key never enters engine function metadata (see ``gateway.py``).
        config = {
            key: value
            for key, value in (snapshot.get("parameters") or {}).items()
            if key in FORWARDED_PARAMETERS or key == "timeout_ms"
        }
        config.update(
            model=model_name, api_key=alias_token(alias["id"]), endpoint=gateway_endpoint()
        )
        config_escaped = json.dumps(config, separators=(",", ": ")).replace("'", "''")
        if alias.get("system_prompt"):
            body = _body_with_prompt(function_type, alias["system_prompt"], config_escaped)
        else:
            body = template["body"].replace("{config}", config_escaped)
    create_sql = f"CREATE GLOBAL FUNCTION {name}({params_sql})\n        RETURNS {body}"
    return RenderedFunction(function_type, name, drop_sql, create_sql, body)


def function_body_matches(rendered: RenderedFunction, body: str) -> bool:
    """Compare complete expressions, including prompts and every provider parameter.

    Use Nova's parser to ignore identifier quoting, keyword case and layout.
    String contents remain exact except for the JSON gateway configuration,
    whose property order and formatting have no effect on its meaning.
    An unsupported engine rendering is unverifiable and never proves success.
    """
    from app.sql_frontend.errors import SQLSyntaxError
    from app.sql_frontend.parser import parse_statement

    def tokens(expression: str) -> list[tuple[str, str]]:
        result = []
        for token in parse_statement("SELECT " + expression).visible_tokens:
            text = token.text
            if text.startswith(("'", '"')):
                quote = text[0]
                value = text[1:-1].replace(quote * 2, quote)
                try:
                    config = json.loads(value)
                except (ValueError, TypeError):
                    config = None
                if isinstance(config, dict) and {"endpoint", "api_key", "model"} <= config.keys():
                    value = json.dumps(config, sort_keys=True, separators=(",", ":"))
                result.append(("literal", value))
            else:
                result.append(("syntax", text.strip("`").lower()))
        return result

    if len(body) > 262144:
        return False
    try:
        return tokens(body) == tokens(rendered.body)
    except (SQLSyntaxError, ValueError, TypeError):
        return False


def _status_item(function_type: str, alias: dict | None, registered: bool) -> dict:
    return {
        "function_name": UDF_TEMPLATES[function_type]["function_name"],
        "function_type": function_type,
        "alias_name": alias.get("alias_name") if alias else None,
        "provider_name": alias.get("provider_name") if alias else None,
        "model_name": alias.get("model_name") if alias else None,
        "registered": registered,
        "error": None,
    }


class LLMFunctionService:
    """Alias configuration and the engine status of the ``AI_*`` functions."""

    @staticmethod
    async def _connect() -> asyncmy.Connection:
        """Engine connection used only to observe or apply ``AI_*`` functions."""
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=settings.STARROCKS_ROOT_USER,
            password=settings.STARROCKS_ROOT_PASSWORD,
            autocommit=True,
            connect_timeout=10,
        )

    # ── Alias reads ─────────────────────────────────────────────

    @classmethod
    async def _joined(
        cls, connection: Any, alias_id: str | None = None
    ) -> list[dict]:
        aliases_repo = ModelAliasRepository(connection)
        if alias_id is None:
            aliases = await aliases_repo.aliases()
        else:
            row = await aliases_repo.alias(alias_id)
            aliases = [row] if row and not row["is_deleted"] else []
        if not aliases:
            return []
        registry = AIRegistryRepository(connection)
        providers = {p["id"]: p for p in await registry.providers()}
        models = {m["id"]: m for m in await registry.models()}
        result = []
        for alias in aliases:
            provider = providers.get(alias["provider_id"]) or {}
            model = models.get(alias["model_id"]) if alias["model_id"] else None
            result.append(cls._deserialize_row({
                **{key: alias[key] for key in (
                    "id", "alias_name", "function_type", "provider_id", "model_id",
                    "system_prompt", "default_params", "is_default", "is_active",
                    "created_at", "updated_at", "created_by",
                )},
                "metadata_revision": alias["metadata_revision"],
                "provider_name": provider.get("name"),
                "model_name": model["name"] if model else None,
                "model_display_name": model["display_name"] if model else None,
            }))
        return result

    async def list_aliases(self, *, actor: SecurityContext | None = None) -> list[dict]:
        """Every live alias with its provider and model names."""
        async with metadata_scope(actor or REGISTRY_READER, read_only=True) as connection:
            return await self._joined(connection)

    async def get_alias(
        self, alias_id: str, *, actor: SecurityContext | None = None
    ) -> dict | None:
        async with metadata_scope(actor or REGISTRY_READER, read_only=True) as connection:
            rows = await self._joined(connection, alias_id)
        return rows[0] if rows else None

    # ── Alias commands ──────────────────────────────────────────

    @staticmethod
    def _definition(data: dict[str, Any]) -> AliasDefinition:
        params = data.get("default_params")
        return AliasDefinition(
            alias_name=data["alias_name"],
            function_type=data["function_type"],
            provider_id=data["provider_id"],
            model_id=data.get("model_id"),
            system_prompt=data.get("system_prompt"),
            default_params=json.dumps(params) if params else None,
            is_default=data.get("is_default") is not False,
            is_active=data.get("is_active") is not False,
        )

    async def create_alias(
        self, data: dict, created_by: str, *, actor: SecurityContext
    ) -> dict:
        """Create an alias; its function definition and registration intent commit with it.

        ``created_by`` is recorded from the bound actor, which is the same user.
        """
        alias_id = str(uuid4())
        definition = self._definition({**data, "is_active": True})
        async with metadata_scope(actor) as connection:
            await AIConfigurationService(connection).create_alias(alias_id, definition)
            rows = await self._joined(connection, alias_id)
        if not rows:
            raise RuntimeError("Created function alias is unavailable")
        return rows[0]

    async def update_alias(
        self, alias_id: str, data: dict, *, actor: SecurityContext
    ) -> dict | None:
        """Merge the supplied fields into the live alias (revision-checked)."""
        llm_gateway.forget(alias_id)
        async with metadata_scope(actor) as connection:
            current = await self._joined(connection, alias_id)
            if not current:
                return None
            existing = current[0]
            merged = {**existing, **{k: v for k, v in data.items() if v is not None}}
            if "system_prompt" in data:
                merged["system_prompt"] = data["system_prompt"]
            if "model_id" in data:
                merged["model_id"] = data["model_id"]
            await AIConfigurationService(connection).replace_alias(
                alias_id,
                self._definition(merged),
                expected_revision=existing["metadata_revision"],
            )
            rows = await self._joined(connection, alias_id)
        return rows[0] if rows else None

    async def delete_alias(self, alias_id: str, *, actor: SecurityContext) -> bool:
        """Delete an alias; the next active alias of its type becomes the default."""
        llm_gateway.forget(alias_id)
        async with metadata_scope(actor) as connection:
            existing = await ModelAliasRepository(connection).alias(alias_id)
            if existing is None or existing["is_deleted"]:
                return False
            await AIConfigurationService(connection).delete_alias(
                alias_id, expected_revision=existing["metadata_revision"]
            )
        return True

    # ── Function registration ───────────────────────────────────

    async def register_all_udfs(self, *, actor: SecurityContext | None = None) -> dict:
        """Persist registration intent for every function whose definition changed.

        The maintenance participant applies and verifies the engine functions;
        this call never drops or creates one. Without an administrator actor
        (process startup) nothing is written: a process start is not a
        configuration change.
        """
        if actor is not None:
            async with metadata_scope(actor) as connection:
                await ModelAliasRepository(connection).lock_mutations()
                jobs = await FunctionRegistrationRepository(connection).reconcile_changed()
            logger.info("Queued %d AI function registration(s)", len(jobs))
        details = await self.get_udf_status(actor=actor)
        registered = sum(1 for item in details if item["registered"])
        return {
            "success": True,
            "registered": registered,
            "failed": len(details) - registered,
            "details": details,
        }

    async def _engine_functions(self) -> set[str]:
        conn = await self._connect()
        names: set[str] = set()
        try:
            async with conn.cursor() as cur:
                await cur.execute("SHOW GLOBAL FUNCTIONS")
                for row in await cur.fetchall():
                    name = row[0] if isinstance(row, tuple) else row.get("Function", "")
                    if name:
                        names.add(str(name).split("(", 1)[0].strip().upper())
        finally:
            conn.close()
        return names

    async def get_udf_status(self, *, actor: SecurityContext | None = None) -> list[dict]:
        """Which ``AI_*`` functions the engine currently has, per selected alias."""
        registered = await self._engine_functions()
        selected: dict[str, dict] = {}
        for alias in await self.list_aliases(actor=actor):
            function_type = alias["function_type"]
            if function_type not in selected or alias.get("is_default"):
                selected[function_type] = alias
        return [
            _status_item(
                function_type, selected.get(function_type),
                template["function_name"] in registered,
            )
            for function_type, template in UDF_TEMPLATES.items()
        ]

    @staticmethod
    def _deserialize_row(row: dict) -> dict:
        """Parse the stored JSON text of ``default_params``."""
        result = dict(row)
        if result.get("default_params") and isinstance(result["default_params"], str):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                result["default_params"] = json.loads(result["default_params"])
        return result


# Singleton
llm_function_service = LLMFunctionService()
