"""Value-level credential redaction for assistant tool results (spec §5.4).

Distinct from :func:`app.common.sql_guard.redact_sql_credentials`, which
redacts **SQL text** (``'aws.s3.secret_key'='AKIA…'``). This module redacts
credential-shaped **values inside result rows** before they can enter the model
context: a ``SELECT * FROM information_schema`` or a table that happens to hold
key-shaped cells would otherwise put a credential straight into the provider
request.

Two rules, both deliberately narrow so ordinary data survives:

* a **column name** that looks credential-shaped (``password``, ``api_key``,
  ``access_key``, …) has all of its cells replaced, regardless of value shape;
* any value that itself matches a known credential format (AWS access key id,
  PEM private key header, ``sk-``/``ghp_``/``xox…`` token, JWT) is replaced even
  when its column name is innocuous.

``None`` is preserved as ``None`` (absent, not redacted) and non-strings are
inspected only after ``str()`` so an integer column named ``token`` is still
covered. Nothing here is a second SQL redactor; it never touches SQL.
"""

from __future__ import annotations

import re

#: Placeholder written in place of a redacted cell. Matches the value redactor
#: used by ``SanitizingJSONResponse`` so a client sees one spelling.
REDACTED_VALUE = "***"

#: Substrings in a column *name* that mark every cell in that column as
#: credential-shaped. Matched case-insensitively on a normalized name where
#: separators are collapsed **and camelCase/PascalCase boundaries are split**,
#: so ``api_key``, ``apiKey``, ``api key`` and ``API-KEY`` all hit, and
#: ``userPassword`` / ``user_password`` normalize to the same ``user password``.
#: Run-together names with no boundary to split (``hashedpassword``,
#: ``dbpass``) are additionally matched on the space-stripped form.
_CREDENTIAL_COLUMN_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "private_key",
    "account_key",
    "sas_token",
    "session_token",
    "credential",
    "client_secret",
    "auth_token",
    "bearer",
    "api token",
    "access_token",
    "refresh_token",
    "id_token",
    "db_password",
    "db_pass",
    "pwd",
)

#: Value formats that are credential-shaped regardless of the column they are
#: in. Each is anchored tightly enough that ordinary data (a sentence, an id)
#: does not match.
_CREDENTIAL_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # AWS access key id (AKIA/ASIA/AGPA/… + 16 base32 chars).
    re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}\b"),
    # PEM private key block.
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    # OpenAI-style / generic provider keys and GitHub tokens. The separator
    # after the prefix is `-` or `_` (``ghp_…`` and ``sk-…`` are both real).
    re.compile(r"\b(?:sk|gsk|xai|ghp|gho|ghu|ghs|ghr)[-_][A-Za-z0-9_\-]{16,}\b"),
    # Slack tokens.
    re.compile(r"\bxox[baprs][-_][A-Za-z0-9-]{10,}\b"),
    # JWT: three base64url segments.
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
)

_SEPARATORS = re.compile(r"[\s\-_.]+")

#: camelCase / PascalCase boundaries. The first splits ``userPassword`` and
#: ``dbPass`` after a lower-case or digit; the second splits an acronym run from
#: the following word so ``APIKey`` becomes ``API Key`` rather than ``AP IKey``.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")


def _normalize(text: str) -> str:
    """Lower-case and collapse ``-``/``_``/``.``/space runs to one space.

    Applied to both the column name and the pattern list, so ``api_key``,
    ``apiKey``, ``api key`` and ``API-KEY`` are the same string on each side.
    camelCase and PascalCase boundaries are split first, so ``userPassword``
    normalizes to ``user password`` and ``APIKey`` to ``api key``.
    """
    spaced = _ACRONYM_BOUNDARY.sub(" ", _CAMEL_BOUNDARY.sub(" ", text))
    return _SEPARATORS.sub(" ", spaced.strip().lower())


_NORMALIZED_COLUMN_PARTS: tuple[str, ...] = tuple(
    _normalize(part) for part in _CREDENTIAL_COLUMN_PARTS
)

#: The same parts with all spaces removed, matched against the run-together
#: column name. Catches names where case folding leaves no boundary to split,
#: e.g. ``hashedpassword``, ``dbpass`` and ``pwd``.
_RUNTOGETHER_COLUMN_PARTS: tuple[str, ...] = tuple(
    part.replace(" ", "") for part in _NORMALIZED_COLUMN_PARTS
)


def _normalized_column(column: str) -> str:
    return _normalize(column)


def is_credential_column(column: str) -> bool:
    """True when a column *name* marks its cells as credential-shaped."""
    normalized = _normalized_column(column)
    if any(f" {part} " in f" {normalized} " for part in _NORMALIZED_COLUMN_PARTS):
        return True
    run_together = normalized.replace(" ", "")
    return any(part in run_together for part in _RUNTOGETHER_COLUMN_PARTS)


def is_credential_value(value: object) -> bool:
    """True when a value itself matches a known credential format."""
    if value is None:
        return False
    text = value if isinstance(value, str) else str(value)
    return any(pattern.search(text) for pattern in _CREDENTIAL_VALUE_PATTERNS)


def redact_row(columns: list[str], row: list) -> list:
    """Return ``row`` with credential-shaped cells replaced.

    A cell is redacted when its column name is credential-shaped **or** the
    value matches a credential format. ``None`` stays ``None``.
    """
    redacted: list = []
    for index, value in enumerate(row):
        column = columns[index] if index < len(columns) else ""
        if value is not None and (
            is_credential_column(column) or is_credential_value(value)
        ):
            redacted.append(REDACTED_VALUE)
        else:
            redacted.append(value)
    return redacted


def redact_rows(columns: list[str], rows: list[list]) -> list[list]:
    """Apply :func:`redact_row` to every row."""
    return [redact_row(columns, row) for row in rows]
