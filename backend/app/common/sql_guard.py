"""SQL guard — blocks dangerous operations on system objects.

Protects ACCOUNTADMIN role and root user from being dropped/modified.
"""

import re

from app.core.exceptions import ForbiddenSQLError

# Nova built-in UDFs — these cannot be dropped by users
BUILTIN_UDFS = [
    "AI_COMPLETE",
    "AI_SENTIMENT",
    "AI_CLASSIFY",
    "AI_SUMMARIZE",
    "AI_EXTRACT",
    "AI_TRANSLATE",
    "AI_FILTER",
    "ML_PREDICT",
]

BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (r"DROP\s+ROLE\s+ACCOUNTADMIN", "ACCOUNTADMIN role cannot be dropped"),
    (
        r"REVOKE\s+.*\s+FROM\s+ROLE\s+ACCOUNTADMIN",
        "Cannot revoke privileges from ACCOUNTADMIN",
    ),
    (r"ALTER\s+ROLE\s+ACCOUNTADMIN", "ACCOUNTADMIN role cannot be altered"),
    (r"DROP\s+USER\s+.*root", "root user cannot be dropped"),
    # Guard: prevent dropping Nova built-in UDFs (any signature)
    (
        r"DROP\s+GLOBAL\s+FUNCTION\s+(IF\s+EXISTS\s+)?(AI_COMPLETE|AI_SENTIMENT|AI_CLASSIFY|AI_SUMMARIZE|AI_EXTRACT|AI_TRANSLATE|AI_FILTER|ML_PREDICT)\s*\(",
        "Cannot drop Nova built-in function. These are managed by the system and auto-registered on startup.",
    ),
    # Also guard DROP without signature
    (
        r"DROP\s+GLOBAL\s+FUNCTION\s+(IF\s+EXISTS\s+)?(AI_COMPLETE|AI_SENTIMENT|AI_CLASSIFY|AI_SUMMARIZE|AI_EXTRACT|AI_TRANSLATE|AI_FILTER|ML_PREDICT)\s*;",
        "Cannot drop Nova built-in function. These are managed by the system and auto-registered on startup.",
    ),
]


def guard_sql(sql: str) -> None:
    """Check SQL for dangerous operations. Raises ForbiddenSQLError if blocked.

    Args:
        sql: The SQL statement to check.

    Raises:
        ForbiddenSQLError: If the SQL matches a blocked pattern.
    """
    upper = sql.strip().upper()
    for pattern, message in BLOCKED_PATTERNS:
        if re.search(pattern, upper, re.IGNORECASE):
            raise ForbiddenSQLError(message)


DESTRUCTIVE_SQL_PATTERN = re.compile(
    r"^\s*(DROP|TRUNCATE|ALTER\s+TABLE\s+.+\s+DROP|DELETE\s+FROM|UPDATE\s+)",
    re.IGNORECASE | re.DOTALL,
)


UNSCOPED_MUTATION_PATTERN = re.compile(
    r"^\s*(DELETE\s+FROM|UPDATE\s+)(?!.*\bWHERE\b)",
    re.IGNORECASE | re.DOTALL,
)


def is_destructive_sql(sql: str) -> bool:
    return bool(DESTRUCTIVE_SQL_PATTERN.search(sql))


def is_unscoped_mutation(sql: str) -> bool:
    return bool(UNSCOPED_MUTATION_PATTERN.search(sql))


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL into individual statements, respecting single-quoted strings.

    Semicolons inside single-quoted strings are not treated as separators.
    Empty statements are filtered out.
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_string:
            in_string = True
            current.append(ch)
        elif ch == "'" and in_string:
            # Check for escaped quote ''
            if i + 1 < len(sql) and sql[i + 1] == "'":
                current.append("''")
                i += 2
                continue
            in_string = False
            current.append(ch)
        elif ch == ";" and not in_string:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1
    # Last statement (no trailing semicolon)
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements
