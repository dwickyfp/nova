"""SQL guard — blocks dangerous operations on system objects.

Protects ACCOUNTADMIN role and root user from being dropped/modified.
"""

import re

from app.core.exceptions import ForbiddenSQLError

BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (r"DROP\s+ROLE\s+ACCOUNTADMIN", "ACCOUNTADMIN role cannot be dropped"),
    (
        r"REVOKE\s+.*\s+FROM\s+ROLE\s+ACCOUNTADMIN",
        "Cannot revoke privileges from ACCOUNTADMIN",
    ),
    (r"ALTER\s+ROLE\s+ACCOUNTADMIN", "ACCOUNTADMIN role cannot be altered"),
    (r"DROP\s+USER\s+.*root", "root user cannot be dropped"),
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
