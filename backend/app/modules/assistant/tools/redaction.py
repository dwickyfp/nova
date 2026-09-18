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
#: separators **and case boundaries** are collapsed, so ``api_key``, ``apiKey``,
#: ``api key``, ``API-KEY`` and ``APIKey`` all hit. The camelCase claim is real
#: as of NOVA-81; before that fix this comment was false.
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
    "access_token",
    "credential",
    "client_secret",
    "auth_token",
    "bearer",
    "api token",
)

#: Short abbreviations matched as **whole words** only. ``pwd`` is three
#: characters, so a substring/compact match would fire on unrelated names
#: (``pwd_count`` is fine, but ``bpwd`` should not); a word-boundary test
#: (``db_pwd``, ``user-pwd``, ``userPwd``) is the narrow, correct rule.
_CREDENTIAL_COLUMN_WORDS: tuple[str, ...] = ("pwd", "pass")

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

#: A lower/digit → upper transition inside a run-together name: ``userPassword``
#: → ``user Password``, ``dbPass`` → ``db Pass``.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: An acronym → word transition: ``APIKey`` → ``API Key``, ``DBAuthToken`` →
#: ``DB Auth Token``. Applied after :data:`_CAMEL_BOUNDARY`, so an all-caps
#: prefix followed by a capitalised word splits once, not per letter.
_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")


def _split_case(text: str) -> str:
    """Insert a separator at camelCase/PascalCase/acronym boundaries."""
    return _ACRONYM_BOUNDARY.sub(" ", _CAMEL_BOUNDARY.sub(" ", text))


def _normalize(text: str) -> str:
    """Lower-case and collapse separators, case boundaries included.

    Case transitions are split **before** the separator collapse, so a
    run-together name reaches the same canonical form as its separated spelling:
    ``userPassword``, ``user_password``, ``user-password`` and ``user password``
    all become ``user password``. Without this step the module's own claim that
    camelCase is covered was false (NOVA-81).
    """
    return _SEPARATORS.sub(" ", _split_case(text.strip()).lower())


_NORMALIZED_COLUMN_PARTS: tuple[str, ...] = tuple(
    _normalize(part) for part in _CREDENTIAL_COLUMN_PARTS
)

#: The same parts with every separator removed, so a run-together name matches
#: without needing a word boundary: ``userPassword`` → ``userpassword`` and
#: ``dbPass``/``hashedPassword`` are caught even when case splitting alone is
#: ambiguous. Matching a compact form is safe because the parts are already
#: long, credential-specific words (``password``, ``secretkey``, ``authtoken``).
_COMPACT_COLUMN_PARTS: tuple[str, ...] = tuple(
    part.replace(" ", "") for part in _NORMALIZED_COLUMN_PARTS
)


def _normalized_column(column: str) -> str:
    return _normalize(column)


def is_credential_column(column: str) -> bool:
    """True when a column *name* marks its cells as credential-shaped.

    Handles separated (``user_password``, ``user-password``, ``user password``)
    and run-together (``userPassword``, ``secretKey``, ``accessToken``,
    ``hashedPassword``, ``dbPass``, ``pwd``) spellings alike (NOVA-81).
    """
    normalized = _normalized_column(column)
    padded = f" {normalized} "
    if any(f" {part} " in padded for part in _NORMALIZED_COLUMN_PARTS):
        return True
    if any(f" {word} " in padded for word in _CREDENTIAL_COLUMN_WORDS):
        return True
    compact = normalized.replace(" ", "")
    return any(part in compact for part in _COMPACT_COLUMN_PARTS)


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
