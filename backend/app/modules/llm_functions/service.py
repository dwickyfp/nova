"""LLM Function Management service — alias CRUD and StarRocks SQL UDF registration.

Tables:
  NOVA_SYSTEM.CONFIG_MODEL_ALIASES  — maps function types to provider+model
  NOVA_SYSTEM.CONFIG_AI_PROVIDERS   — provider config (endpoint, api_key)
  NOVA_SYSTEM.CONFIG_AI_MODELS      — model config (name, max_tokens)

Flow:
  1. User creates alias: function_type → provider_id + model_id
  2. Service generates SQL UDF that wraps ai_query() with resolved config
  3. Service executes CREATE FUNCTION in StarRocks
  4. User can now call AI_COMPLETE(), AI_SENTIMENT(), etc. in SQL
"""

import json
import logging
from uuid import uuid4

import asyncmy
import asyncmy.cursors

from app.core.config import settings
from app.common.crypto import decrypt as decrypt_api_key

logger = logging.getLogger(__name__)


# ── UDF Templates ─────────────────────────────────────────────
# Each template wraps ai_query() with a system prompt for the function type.
# The {config} placeholder is replaced with the resolved JSON config string.

UDF_TEMPLATES: dict[str, dict] = {
    "complete": {
        "function_name": "AI_COMPLETE",
        "params": ["prompt STRING"],
        "body": 'ai_query(prompt, \'{config}\')',
        "system_prompt": "",
    },
    "sentiment": {
        "function_name": "AI_SENTIMENT",
        "params": ["txt STRING"],
        "body": 'ai_query(CONCAT(\'Analyze the sentiment of the following text. Reply with JSON: {"sentiment": "positive|negative|neutral|mixed", "confidence": 0.0-1.0}\\n\\nText: \', txt), \'{config}\')',
        "system_prompt": 'Analyze the sentiment of the following text. Reply with JSON: {"sentiment": "positive|negative|neutral|mixed", "confidence": 0.0-1.0}\n\nText: ',
    },
    "classify": {
        "function_name": "AI_CLASSIFY",
        "params": ["txt STRING", "categories STRING"],
        "body": 'ai_query(CONCAT(\'Classify the following text into ONE of these categories: [\', categories, \']. Reply with ONLY the category name, nothing else.\\n\\nText: \', txt), \'{config}\')',
        "system_prompt": 'Classify the following text into ONE of these categories: [{categories}]. Reply with ONLY the category name, nothing else.\n\nText: ',
    },
    "summarize": {
        "function_name": "AI_SUMMARIZE",
        "params": ["txt STRING"],
        "body": 'ai_query(CONCAT(\'Summarize the following text concisely in 2-3 sentences:\\n\\n\', txt), \'{config}\')',
        "system_prompt": 'Summarize the following text concisely in 2-3 sentences:\n\n',
    },
    "extract": {
        "function_name": "AI_EXTRACT",
        "params": ["txt STRING", "json_schema STRING"],
        "body": 'ai_query(CONCAT(\'Extract structured information from the following text. Return as JSON matching this schema: \', json_schema, \'. Reply with ONLY the JSON.\\n\\nText: \', txt), \'{config}\')',
        "system_prompt": 'Extract structured information from the following text. Return as JSON matching this schema: {json_schema}. Reply with ONLY the JSON.\n\nText: ',
    },
    "translate": {
        "function_name": "AI_TRANSLATE",
        "params": ["txt STRING", "target_lang STRING"],
        "body": 'ai_query(CONCAT(\'Translate the following text to \', target_lang, \'. Reply with ONLY the translation, no explanation.\\n\\nText: \', txt), \'{config}\')',
        "system_prompt": 'Translate the following text to {target_lang}. Reply with ONLY the translation, no explanation.\n\nText: ',
    },
    "filter": {
        "function_name": "AI_FILTER",
        "params": ["txt STRING", "criteria STRING"],
        "body": 'ai_query(CONCAT(\'Does the following text match this criteria? Reply with ONLY \"true\" or \"false\".\\nCriteria: \', criteria, \'\\n\\nText: \', txt), \'{config}\')',
        "system_prompt": 'Does the following text match this criteria? Reply with ONLY "true" or "false".\nCriteria: {criteria}\n\nText: ',
    },
}


class LLMFunctionService:
    """Manage LLM function aliases and register SQL UDFs in StarRocks.

    Every method opens a fresh root-level asyncmy connection, executes,
    and closes. This keeps the service stateless and avoids pool contention.
    """

    # ── DB helpers ──────────────────────────────────────────────

    @staticmethod
    async def _connect() -> asyncmy.Connection:
        """Open a root-level connection to StarRocks."""
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=settings.STARROCKS_ROOT_USER,
            password=settings.STARROCKS_ROOT_PASSWORD,
            autocommit=True,
            connect_timeout=10,
        )

    # ── Alias CRUD ──────────────────────────────────────────────

    async def list_aliases(self) -> list[dict]:
        """List all aliases with provider and model names."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT a.id, a.alias_name, a.function_type,
                           a.provider_id, a.model_id,
                           a.system_prompt, a.default_params,
                           a.is_default, a.is_active,
                           a.created_at, a.updated_at, a.created_by,
                           p.name AS provider_name,
                           m.name AS model_name,
                           m.display_name AS model_display_name
                    FROM NOVA_SYSTEM.CONFIG_MODEL_ALIASES a
                    LEFT JOIN NOVA_SYSTEM.CONFIG_AI_PROVIDERS p
                        ON p.id = a.provider_id
                    LEFT JOIN NOVA_SYSTEM.CONFIG_AI_MODELS m
                        ON m.id = a.model_id
                    ORDER BY a.function_type, a.alias_name
                    """
                )
                rows = await cur.fetchall()
                return [self._deserialize_row(row) for row in rows]
        finally:
            conn.close()

    async def get_alias(self, alias_id: str) -> dict | None:
        """Get a single alias by ID."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT a.id, a.alias_name, a.function_type,
                           a.provider_id, a.model_id,
                           a.system_prompt, a.default_params,
                           a.is_default, a.is_active,
                           a.created_at, a.updated_at, a.created_by,
                           p.name AS provider_name,
                           m.name AS model_name,
                           m.display_name AS model_display_name
                    FROM NOVA_SYSTEM.CONFIG_MODEL_ALIASES a
                    LEFT JOIN NOVA_SYSTEM.CONFIG_AI_PROVIDERS p
                        ON p.id = a.provider_id
                    LEFT JOIN NOVA_SYSTEM.CONFIG_AI_MODELS m
                        ON m.id = a.model_id
                    WHERE a.id = %s
                    """,
                    (alias_id,),
                )
                row = await cur.fetchone()
                return self._deserialize_row(row) if row else None
        finally:
            conn.close()

    async def create_alias(self, data: dict, created_by: str) -> dict:
        """Create a new alias and register the UDF."""
        alias_id = str(uuid4())
        default_params = data.get("default_params")
        params_str = json.dumps(default_params) if default_params else None

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                # If is_default, unset other defaults for same function_type
                if data.get("is_default", True):
                    await cur.execute(
                        """
                        UPDATE NOVA_SYSTEM.CONFIG_MODEL_ALIASES
                        SET is_default = false
                        WHERE function_type = %s AND is_default = true
                        """,
                        (data["function_type"],),
                    )

                await cur.execute(
                    """
                    INSERT INTO NOVA_SYSTEM.CONFIG_MODEL_ALIASES
                        (id, alias_name, function_type, provider_id, model_id,
                         system_prompt, default_params, is_default, is_active,
                         created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, %s)
                    """,
                    (
                        alias_id,
                        data["alias_name"],
                        data["function_type"],
                        data["provider_id"],
                        data.get("model_id"),
                        data.get("system_prompt"),
                        params_str,
                        data.get("is_default", True),
                        created_by,
                    ),
                )
        finally:
            conn.close()

        # Register the UDF for this function type
        await self.register_udf_for_type(data["function_type"])

        result = await self.get_alias(alias_id)
        return result

    async def update_alias(self, alias_id: str, data: dict) -> dict | None:
        """Update an existing alias."""
        existing = await self.get_alias(alias_id)
        if not existing:
            return None

        # Build SET clause
        set_parts: list[str] = []
        values: list = []
        field_map = {
            "alias_name": "alias_name",
            "function_type": "function_type",
            "provider_id": "provider_id",
            "is_default": "is_default",
            "is_active": "is_active",
        }
        for key, col in field_map.items():
            if key in data and data[key] is not None:
                set_parts.append(f"{col} = %s")
                values.append(data[key])

        if "system_prompt" in data:
            set_parts.append("system_prompt = %s")
            values.append(data["system_prompt"])

        if "default_params" in data and data["default_params"] is not None:
            set_parts.append("default_params = %s")
            values.append(json.dumps(data["default_params"]))

        if "model_id" in data:
            set_parts.append("model_id = %s")
            values.append(data["model_id"])

        if not set_parts:
            return existing

        set_parts.append("updated_at = CURRENT_TIMESTAMP")
        values.append(alias_id)

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                # If setting as default, unset others
                if data.get("is_default"):
                    await cur.execute(
                        """
                        UPDATE NOVA_SYSTEM.CONFIG_MODEL_ALIASES
                        SET is_default = false
                        WHERE function_type = %s AND is_default = true
                          AND id != %s
                        """,
                        (existing["function_type"], alias_id),
                    )

                await cur.execute(
                    f"""
                    UPDATE NOVA_SYSTEM.CONFIG_MODEL_ALIASES
                    SET {', '.join(set_parts)}
                    WHERE id = %s
                    """,
                    tuple(values),
                )
        finally:
            conn.close()

        # Re-register the UDF
        function_type = data.get("function_type", existing["function_type"])
        await self.register_udf_for_type(function_type)

        return await self.get_alias(alias_id)

    async def delete_alias(self, alias_id: str) -> bool:
        """Delete an alias. If it was the default, try to promote another."""
        existing = await self.get_alias(alias_id)
        if not existing:
            return False

        function_type = existing["function_type"]
        was_default = existing["is_default"]

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_MODEL_ALIASES WHERE id = %s",
                    (alias_id,),
                )
        finally:
            conn.close()

        # If deleted alias was default, promote the next one
        if was_default:
            aliases = await self.list_aliases()
            next_alias = next(
                (a for a in aliases if a["function_type"] == function_type and a["is_active"]),
                None,
            )
            if next_alias:
                await self.update_alias(next_alias["id"], {"is_default": True})

        return True

    # ── UDF Registration ────────────────────────────────────────

    async def register_all_udfs(self) -> dict:
        """Register UDFs for all function types that have a default alias.

        Called on backend startup and when aliases change.
        """
        aliases = await self.list_aliases()
        # Group by function_type, pick the default (or first active)
        type_to_alias: dict[str, dict] = {}
        for alias in aliases:
            ft = alias["function_type"]
            if ft not in type_to_alias or alias.get("is_default"):
                type_to_alias[ft] = alias

        results: list[dict] = []
        registered_count = 0
        failed_count = 0

        # Register UDFs for all types that have aliases
        for ft, alias in type_to_alias.items():
            if not alias.get("is_active", True):
                continue
            result = await self._register_single_udf(ft, alias)
            results.append(result)
            if result["registered"]:
                registered_count += 1
            else:
                failed_count += 1

        # Also register "empty" UDFs for types without aliases
        # so that SQL queries don't fail with "function not found"
        for ft, template in UDF_TEMPLATES.items():
            if ft not in type_to_alias:
                result = await self._register_placeholder_udf(ft)
                results.append(result)
                if result["registered"]:
                    registered_count += 1
                else:
                    failed_count += 1

        # Grant USAGE on all registered UDFs to all roles
        # so they behave like native built-in functions
        await self._grant_udf_privileges()

        return {
            "success": failed_count == 0,
            "registered": registered_count,
            "failed": failed_count,
            "details": results,
        }

    async def _grant_udf_privileges(self) -> None:
        """Grant USAGE on all Nova built-in UDFs to all roles.

        This makes AI_* and ML_* functions behave like native built-ins
        that every user can call without explicit grants.
        Uses both STRING and VARCHAR(65533) signatures because StarRocks
        internally maps string→varchar for Java UDFs.
        """
        # All UDF signatures — grant both STRING and VARCHAR(65533)
        # because SQL UDFs use STRING but Java UDFs register as VARCHAR
        udf_signatures = [
            ("AI_COMPLETE", ["(STRING)", "(VARCHAR)", "(VARCHAR(65533))"]),
            ("AI_SENTIMENT", ["(STRING)", "(VARCHAR)", "(VARCHAR(65533))"]),
            ("AI_CLASSIFY", ["(STRING, STRING)", "(VARCHAR, VARCHAR)", "(VARCHAR(65533), VARCHAR(65533))"]),
            ("AI_SUMMARIZE", ["(STRING)", "(VARCHAR)", "(VARCHAR(65533))"]),
            ("AI_EXTRACT", ["(STRING, STRING)", "(VARCHAR, VARCHAR)", "(VARCHAR(65533), VARCHAR(65533))"]),
            ("AI_TRANSLATE", ["(STRING, STRING)", "(VARCHAR, VARCHAR)", "(VARCHAR(65533), VARCHAR(65533))"]),
            ("AI_FILTER", ["(STRING, STRING)", "(VARCHAR, VARCHAR)", "(VARCHAR(65533), VARCHAR(65533))"]),
            ("ML_PREDICT", ["(STRING, STRING)", "(VARCHAR, VARCHAR)", "(VARCHAR(65533), VARCHAR(65533))"]),
        ]

        # Roles to grant to (covers all users)
        roles = ["root", "db_admin", "cluster_admin", "user_admin", "ACCOUNTADMIN"]

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                for fn_name, sigs in udf_signatures:
                    for sig in sigs:
                        for role in roles:
                            try:
                                await cur.execute(
                                    f"GRANT USAGE ON GLOBAL FUNCTION {fn_name}{sig} TO ROLE {role}"
                                )
                            except Exception:
                                pass  # Signature/role might not exist, skip silently
        finally:
            conn.close()
        logger.info("Granted USAGE on %d UDF signatures to %d roles", 
                     sum(len(s) for _, s in udf_signatures), len(roles))

    async def register_udf_for_type(self, function_type: str) -> dict:
        """Register the UDF for a specific function type."""
        aliases = await self.list_aliases()
        # Find the default alias for this type
        alias = next(
            (a for a in aliases if a["function_type"] == function_type and a.get("is_default")),
            None,
        )
        if not alias:
            # Try any active alias for this type
            alias = next(
                (a for a in aliases if a["function_type"] == function_type and a.get("is_active", True)),
                None,
            )

        if not alias:
            return await self._register_placeholder_udf(function_type)

        return await self._register_single_udf(function_type, alias)

    async def _register_single_udf(self, function_type: str, alias: dict) -> dict:
        """Register a UDF with resolved provider config."""
        template = UDF_TEMPLATES.get(function_type)
        if not template:
            return {
                "function_name": function_type.upper(),
                "function_type": function_type,
                "alias_name": alias.get("alias_name"),
                "provider_name": alias.get("provider_name"),
                "model_name": alias.get("model_name"),
                "registered": False,
                "error": f"Unknown function type: {function_type}",
            }

        # Resolve provider config
        provider = await self._get_provider(alias["provider_id"])
        if not provider:
            return {
                "function_name": template["function_name"],
                "function_type": function_type,
                "alias_name": alias.get("alias_name"),
                "provider_name": None,
                "model_name": alias.get("model_name"),
                "registered": False,
                "error": f"Provider {alias['provider_id']} not found",
            }

        # Resolve model name
        model_name = alias.get("model_name")
        if not model_name and alias.get("model_id"):
            model = await self._get_model(alias["model_id"])
            if model:
                model_name = model["name"]

        # Build config JSON for ai_query()
        # NOTE: StarRocks ai_query() config fields:
        #   - model (required): model name
        #   - api_key (required): API key for the LLM provider
        #   - endpoint_url (optional): custom endpoint URL
        config = {
            "model": model_name or "gpt-4o-mini",
            "api_key": decrypt_api_key(provider["api_key"]),
        }
        # Add endpoint if provider has a custom one
        # StarRocks ai_query() uses "endpoint" field (NOT "endpoint_url")
        # and requires full path to /chat/completions
        if provider.get("endpoint"):
            endpoint = provider["endpoint"]
            # Docker BE can't reach host localhost; use host.docker.internal
            endpoint = endpoint.replace("localhost", "host.docker.internal")
            endpoint = endpoint.replace("127.0.0.1", "host.docker.internal")
            # ai_query() needs full path including /chat/completions
            if not endpoint.endswith("/chat/completions"):
                # Remove trailing slash
                endpoint = endpoint.rstrip("/")
                # Add /chat/completions if not already there
                if endpoint.endswith("/v1"):
                    endpoint = endpoint + "/chat/completions"
                elif not endpoint.endswith("/chat/completions"):
                    endpoint = endpoint + "/v1/chat/completions"
            config["endpoint"] = endpoint

        # Merge default_params from alias
        if alias.get("default_params"):
            config.update(alias["default_params"])

        config_str = json.dumps(config, separators=(",", ": "))
        # Escape single quotes for SQL string literal
        config_escaped = config_str.replace("'", "''")

        # Use custom system prompt if provided, otherwise template default
        if alias.get("system_prompt"):
            # Custom prompt — we need to build the body differently
            body = self._build_body_with_prompt(
                function_type, alias["system_prompt"], config_escaped
            )
        else:
            body = template["body"].replace("{config}", config_escaped)

        # StarRocks SQL UDF syntax: CREATE GLOBAL FUNCTION name(arg type) RETURNS expr
        params_str = ", ".join(template["params"])
        fn_name = template["function_name"]

        # Drop existing function first (StarRocks doesn't support CREATE OR REPLACE for UDFs)
        # Try multiple type signatures since StarRocks may store as STRING or VARCHAR
        drop_sql = f"DROP GLOBAL FUNCTION IF EXISTS {fn_name}({', '.join('STRING' for _ in template['params'])})"

        sql = f"""CREATE GLOBAL FUNCTION {fn_name}({params_str})
        RETURNS {body}"""

        try:
            conn = await self._connect()
            try:
                async with conn.cursor() as cur:
                    # Drop existing function first
                    await cur.execute(drop_sql)
                    # Create new function
                    await cur.execute(sql)
            finally:
                conn.close()

            logger.info("Registered UDF %s (provider=%s, model=%s)",
                        fn_name, provider["name"], model_name)
            return {
                "function_name": fn_name,
                "function_type": function_type,
                "alias_name": alias.get("alias_name"),
                "provider_name": provider["name"],
                "model_name": model_name,
                "registered": True,
                "error": None,
            }
        except Exception as e:
            logger.error("Failed to register UDF %s: %s", fn_name, e)
            return {
                "function_name": fn_name,
                "function_type": function_type,
                "alias_name": alias.get("alias_name"),
                "provider_name": provider["name"],
                "model_name": model_name,
                "registered": False,
                "error": str(e),
            }

    async def _register_placeholder_udf(self, function_type: str) -> dict:
        """Register a placeholder UDF that returns an error message.

        This ensures SQL queries don't fail with 'function not found'
        when no alias is configured.
        """
        template = UDF_TEMPLATES.get(function_type)
        if not template:
            return {
                "function_name": function_type.upper(),
                "function_type": function_type,
                "alias_name": None,
                "provider_name": None,
                "model_name": None,
                "registered": False,
                "error": f"Unknown function type: {function_type}",
            }

        fn_name = template["function_name"]
        # StarRocks SQL UDF: "name type" format
        params_str = ", ".join(template["params"])
        param_names = [p.split()[0] for p in template["params"]]
        if param_names:
            # Build param=value pairs with ', ' separator between them
            param_pairs = []
            for p in param_names:
                param_pairs.append(f"'{p}='")
                param_pairs.append(p)
                param_pairs.append("', '")
            # Remove last separator
            if param_pairs:
                param_pairs = param_pairs[:-1]
            flat_parts = ", ".join(param_pairs)
            body = f"CONCAT('ERROR: {fn_name} not configured. Set up an alias in AI Providers > Functions tab. Input was: ', {flat_parts})"
        else:
            body = f"'ERROR: {fn_name} not configured.'"

        drop_sql = f"DROP GLOBAL FUNCTION IF EXISTS {fn_name}({', '.join('STRING' for _ in template['params'])})"

        sql = f"""CREATE GLOBAL FUNCTION {fn_name}({params_str})
        RETURNS {body}"""

        try:
            conn = await self._connect()
            try:
                async with conn.cursor() as cur:
                    await cur.execute(drop_sql)
                    await cur.execute(sql)
            finally:
                conn.close()

            logger.info("Registered placeholder UDF %s", fn_name)
            return {
                "function_name": fn_name,
                "function_type": function_type,
                "alias_name": None,
                "provider_name": None,
                "model_name": None,
                "registered": True,
                "error": None,
            }
        except Exception as e:
            logger.error("Failed to register placeholder UDF %s: %s", fn_name, e)
            return {
                "function_name": fn_name,
                "function_type": function_type,
                "alias_name": None,
                "provider_name": None,
                "model_name": None,
                "registered": False,
                "error": str(e),
            }

    async def get_udf_status(self) -> list[dict]:
        """Check which UDFs are registered in StarRocks."""
        conn = await self._connect()
        registered_functions: set[str] = set()
        try:
            async with conn.cursor() as cur:
                await cur.execute("SHOW GLOBAL FUNCTIONS")
                rows = await cur.fetchall()
                for row in rows:
                    # row format: (function_name, return_type, ...)
                    fn_name = row[0] if isinstance(row, tuple) else row.get("Function", "")
                    if fn_name:
                        registered_functions.add(fn_name.upper())
        finally:
            conn.close()

        aliases = await self.list_aliases()
        type_to_alias: dict[str, dict] = {}
        for alias in aliases:
            ft = alias["function_type"]
            if ft not in type_to_alias or alias.get("is_default"):
                type_to_alias[ft] = alias

        results: list[dict] = []
        for ft, template in UDF_TEMPLATES.items():
            fn_name = template["function_name"]
            alias = type_to_alias.get(ft)
            results.append({
                "function_name": fn_name,
                "function_type": ft,
                "alias_name": alias.get("alias_name") if alias else None,
                "provider_name": alias.get("provider_name") if alias else None,
                "model_name": alias.get("model_name") if alias else None,
                "registered": fn_name in registered_functions,
                "error": None,
            })
        return results

    # ── Internal helpers ────────────────────────────────────────

    async def _get_provider(self, provider_id: str) -> dict | None:
        """Get provider by ID."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, type, endpoint, api_key, default_params "
                    "FROM NOVA_SYSTEM.CONFIG_AI_PROVIDERS WHERE id = %s",
                    (provider_id,),
                )
                row = await cur.fetchone()
                if not row:
                    return {}
                result = dict(row)
                if result.get("default_params") and isinstance(result["default_params"], str):
                    try:
                        result["default_params"] = json.loads(result["default_params"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                return result
        finally:
            conn.close()

    async def _get_model(self, model_id: str) -> dict | None:
        """Get model by ID."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, provider_id, name, display_name, type, max_tokens "
                    "FROM NOVA_SYSTEM.CONFIG_AI_MODELS WHERE id = %s",
                    (model_id,),
                )
                row = await cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def _build_body_with_prompt(self, function_type: str, system_prompt: str, config_str: str) -> str:
        """Build ai_query body with a custom system prompt."""
        template = UDF_TEMPLATES[function_type]
        # Escape single quotes in prompt for SQL string
        escaped_prompt = system_prompt.replace("'", "''")

        if function_type == "complete":
            return f"ai_query(CONCAT('{escaped_prompt}', prompt), '{config_str}')"
        elif function_type == "sentiment":
            return f"ai_query(CONCAT('{escaped_prompt}', txt), '{config_str}')"
        elif function_type == "classify":
            return f"ai_query(CONCAT('{escaped_prompt}', categories, ']: ', txt), '{config_str}')"
        elif function_type == "summarize":
            return f"ai_query(CONCAT('{escaped_prompt}', txt), '{config_str}')"
        elif function_type == "extract":
            return f"ai_query(CONCAT('{escaped_prompt}', json_schema, '. Reply with ONLY the JSON.\\n\\nText: ', txt), '{config_str}')"
        elif function_type == "translate":
            return f"ai_query(CONCAT('{escaped_prompt}', target_lang, '. Reply with ONLY the translation.\\n\\nText: ', txt), '{config_str}')"
        elif function_type == "filter":
            return f"ai_query(CONCAT('{escaped_prompt}', criteria, '\\n\\nText: ', txt), '{config_str}')"
        else:
            params = [p.split()[0] for p in template["params"]]
            return f"ai_query(CONCAT('{escaped_prompt}', {', '.join(params)}), '{config_str}')"

    @staticmethod
    def _deserialize_row(row: dict) -> dict:
        """Deserialize a DB row, parsing JSON fields."""
        result = dict(row)
        if result.get("default_params") and isinstance(result["default_params"], str):
            try:
                result["default_params"] = json.loads(result["default_params"])
            except (json.JSONDecodeError, TypeError):
                pass
        return result


# Singleton
llm_function_service = LLMFunctionService()
