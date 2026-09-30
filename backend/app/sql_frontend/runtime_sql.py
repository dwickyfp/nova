from __future__ import annotations

from difflib import SequenceMatcher
from uuid import uuid4

from app.common.sql_guard import redact_sql_credentials
from app.sql_frontend.parser import ParsedStatement
from app.sql_frontend.planning.execution import EngineSqlPlan, PrivateSqlBinding


def private_sql_bindings(parsed: ParsedStatement) -> tuple[PrivateSqlBinding, ...]:
    source = parsed.normalized_sql
    redacted = redact_sql_credentials(source)
    if redacted == source:
        return ()
    changes = [
        (start, end)
        for operation, start, end, _, _ in SequenceMatcher(
            None, source, redacted, autojunk=False
        ).get_opcodes()
        if operation != "equal"
    ]
    nonce = uuid4().hex
    bindings: list[PrivateSqlBinding] = []
    for token in parsed.tokens.tokens:
        start, end = token.start, token.stop + 1
        if start >= 0 and end > start and any(a < end and b > start for a, b in changes):
            # Cover the entire token, including unchanged characters in a secret.
            bindings.append(
                PrivateSqlBinding(f"'***' /* __nova_private_{nonce}_{len(bindings)} */", start, end)
            )
    if not bindings:
        raise ValueError("Sensitive SQL cannot be represented safely")
    return tuple(bindings)


def lower_private_sql(plan: EngineSqlPlan, sql: str, private_source: str) -> str:
    for binding in plan.private_bindings:
        if sql.count(binding.slot) != 1 or not 0 <= binding.start < binding.end <= len(
            private_source
        ):
            raise ValueError("Private SQL slot is missing, duplicated, or out of range")
        sql = sql.replace(binding.slot, private_source[binding.start : binding.end], 1)
    return sql
