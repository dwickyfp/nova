"""OpenUI composition and validation inside the bounded assistant engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.modules.assistant.tools.redaction import is_credential_column, is_credential_value

RUNTIME = Path(__file__).with_name("ui_runtime")
MAX_PROGRAM_BYTES = 64_000
MAX_STATE_BYTES = 16_384
CATALOG_VERSION = "nova-openui-1"
_FENCE = re.compile(r"```openui[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_RUNTIME_SLOTS = asyncio.Semaphore(4)


class UiValidationError(ValueError):
    pass


def split_program(answer: str) -> tuple[str, str | None]:
    programs = _FENCE.findall(answer)
    fallback = _FENCE.sub("", answer).strip()
    if "```openui" in fallback.lower():
        fallback = re.split(r"```openui", fallback, flags=re.IGNORECASE)[0].strip()
    return fallback, programs[0].strip() if len(programs) == 1 else None


def runtime_available() -> bool:
    return bool(shutil.which("node") and (RUNTIME / "validator.cjs").is_file())


@lru_cache(maxsize=1)
def catalog() -> dict:
    value = json.loads((RUNTIME / "catalog.json").read_text())
    digest = hashlib.sha256((RUNTIME / "validator.cjs").read_bytes()).hexdigest()
    if value["version"] != CATALOG_VERSION or value["bundle_sha256"] != digest:
        raise UiValidationError("UI catalog and validator do not match")
    return value


def composer_prompt(*, enabled: bool, presentation: str, token_room: int) -> str:
    if not enabled or presentation != "interactive" or not settings.STUDIO_INTELLIGENT_UI_ENABLED:
        return ""
    if not runtime_available():
        return ""
    try:
        prompt = catalog()["prompt"]
    except (OSError, KeyError, ValueError):
        return ""
    return prompt if len(prompt) // 3 + 512 <= token_room else ""


def validate_state(state: Any, defaults: dict | None = None) -> dict:
    if not isinstance(state, dict) or len(state) > 64:
        raise UiValidationError("UI state must contain declared variables")
    try:
        encoded = json.dumps(state, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise UiValidationError("UI state must be declarative JSON") from exc
    if len(encoded.encode()) > MAX_STATE_BYTES:
        raise UiValidationError("UI state is too large")

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 6:
            raise UiValidationError("UI state is too deeply nested")
        if isinstance(value, str):
            if len(value) > 4000 or is_credential_value(value):
                raise UiValidationError("UI state contains a secret or oversized value")
        elif value is None or isinstance(value, (bool, int, float)):
            return
        elif isinstance(value, list) and len(value) <= 100:
            for item in value:
                visit(item, depth + 1)
        elif isinstance(value, dict) and len(value) <= 64:
            for key, item in value.items():
                if (
                    not isinstance(key, str)
                    or key in {"__proto__", "constructor", "prototype"}
                    or (
                        is_credential_column(key)
                        or re.search(r"result|evidence|credential", key, re.I)
                    )
                ):
                    raise UiValidationError("UI state contains reserved fields")
                visit(item, depth + 1)
        else:
            raise UiValidationError("UI state must contain bounded declarative values")

    visit(state)
    if defaults is not None:
        for key, value in state.items():
            if key not in defaults or not key.startswith("$"):
                raise UiValidationError("Undeclared UI state")
            base = defaults[key]
            if base is not None and (
                isinstance(base, bool) != isinstance(value, bool)
                or isinstance(base, list) != isinstance(value, list)
                or isinstance(base, dict) != isinstance(value, dict)
                or isinstance(base, str) != isinstance(value, str)
                or isinstance(base, (int, float)) != isinstance(value, (int, float))
            ):
                raise UiValidationError("UI state type changed")
    return state


def _records(tables: dict[str, dict]) -> dict[str, list[dict]]:
    out = {}
    for evidence_id, table in tables.items():
        columns = table.get("columns") or []
        names = [str(c.get("name", "")) if isinstance(c, dict) else str(c) for c in columns]
        if len(names) > 50 or any(is_credential_column(name) for name in names):
            continue
        records = []
        for row in (table.get("rows") or [])[:200]:
            values = list(row.values()) if isinstance(row, dict) else row
            if not isinstance(values, (tuple, list)) or any(is_credential_value(v) for v in values):
                continue
            records.append(dict(zip(names, values, strict=False)))
        out[evidence_id] = records
    return out


async def parse_program(
    program: str, *, tables: dict | None = None, business: bool = False,
    state: dict | None = None, private_context: bool = False,
) -> dict:
    if not program or len(program.encode()) > MAX_PROGRAM_BYTES or is_credential_value(program):
        raise UiValidationError("Invalid UI program")
    if not runtime_available():
        raise UiValidationError("UI validator runtime is unavailable")
    catalog()
    data = {"program": program, "evidence": _records(tables or {}), "business": business,
            "private_context": private_context}
    if state is not None:
        data["state"] = validate_state(state)
    payload = json.dumps(data, default=str, allow_nan=False).encode()
    if len(payload) > 512_000:
        raise UiValidationError("UI evidence exceeds its limit")
    process = None
    try:
        async with asyncio.timeout(5), _RUNTIME_SLOTS:
            process = await asyncio.create_subprocess_exec(
                shutil.which("node"),
                "--max-old-space-size=64",
                "--disallow-code-generation-from-strings",
                str(RUNTIME / "validator.cjs"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", ""), "OPENUI_TELEMETRY_DISABLED": "1"},
                limit=128_000,
            )
            stdout, _ = await process.communicate(payload)
            if len(stdout) > 128_000:
                raise UiValidationError("UI validation output exceeds its limit")
            result = json.loads(stdout)
            if process.returncode or not result.get("ok"):
                raise UiValidationError("Program or state failed Nova UI validation")
            validate_state(result["state"])
            return result
    except (OSError, ValueError, TimeoutError) as exc:
        raise UiValidationError("Program or state failed Nova UI validation") from exc
    finally:
        if process and process.returncode is None:
            process.kill()
            await process.wait()


async def compose_block(
    program: str,
    fallback: str,
    *,
    tables: dict,
    business: bool,
    question: str,
    claims: Any,
    language: str,
    private_context: bool = False,
) -> dict:
    if not fallback.strip():
        raise UiValidationError("Interactive answers require a Markdown fallback")
    result = await parse_program(program, tables=tables, business=business,
                                 private_context=private_context)
    if business:
        from app.modules.assistant.answer_contract import check_numeric_answer

        check = check_numeric_answer(
            result["display_text"],
            question=question,
            tables=tables,
            claims=claims,
            language=language,
        )
        if check.unsupported:
            raise UiValidationError("UI displays numbers unsupported by verified evidence")
    bindings = _records(tables)
    ids = result["evidence_ids"]
    return {
        "catalog_version": CATALOG_VERSION,
        "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
        "program": program,
        "fallback": fallback,
        "state_defaults": result["state"],
        "actions": result["actions"],
        "evidence": {key: bindings[key] for key in ids},
        "provenance": {
            "evidence_ids": ids,
            "scope": "verified_result_rows" if ids else "user_simulation",
            "assumption": not bool(ids),
        },
    }
