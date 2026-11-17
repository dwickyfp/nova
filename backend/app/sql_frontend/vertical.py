"""The MySQL client's ``\\G`` terminator in worksheet scripts.

``SHOW PROCESSLIST\\G`` ends a statement like ``;`` and asks for the result one
record per block. The engine does not know ``\\G``; the worksheet removes it
before the statement is checked or run and shows that result vertically.

Only a ``\\G`` outside string literals, quoted identifiers, comments and
``$$`` bodies is a terminator: ``SELECT 'a\\Gb'`` is left as written.
"""

from __future__ import annotations

from app.common.sql_guard import strip_sql_comments


def _skip_quoted(sql: str, start: int, quote: str) -> int:
    """Index just past the literal or quoted identifier opened at ``start``."""
    index = start + 1
    length = len(sql)
    while index < length:
        char = sql[index]
        if char == "\\" and quote != "`" and index + 1 < length:
            index += 2
            continue
        if char == quote:
            if index + 1 < length and sql[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return length


def _terminators(sql: str) -> list[int]:
    """Offsets of every ``\\G`` that ends a statement in ``sql``."""
    found: list[int] = []
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char in {"'", '"', "`"}:
            index = _skip_quoted(sql, index, char)
        elif sql.startswith("--", index) or char == "#":
            newline = sql.find("\n", index)
            index = length if newline < 0 else newline
        elif sql.startswith("/*", index):
            close = sql.find("*/", index + 2)
            index = length if close < 0 else close + 2
        elif sql.startswith("$$", index):
            close = sql.find("$$", index + 2)
            index = length if close < 0 else close + 2
        elif char == "\\" and index + 1 < length and sql[index + 1] == "G":
            found.append(index)
            index += 2
        else:
            index += 1
    return found


def split_vertical(statement: str) -> list[tuple[str, bool]]:
    """Split one statement at ``\\G`` terminators.

    Returns ``(sql, vertical)`` pairs: each piece that ended with ``\\G`` is
    vertical, and a piece after the last ``\\G`` is not. Empty pieces are
    dropped. A statement without ``\\G`` comes back unchanged.
    """
    if "\\G" not in statement:
        return [(statement, False)]
    pieces: list[tuple[str, bool]] = []
    position = 0
    for offset in _terminators(statement):
        piece = statement[position:offset].strip()
        if piece and strip_sql_comments(piece).strip():
            pieces.append((piece, True))
        position = offset + 2
    rest = statement[position:].strip()
    if rest and strip_sql_comments(rest).strip():
        pieces.append((rest, False))
    return pieces or [(statement, False)]


def split_vertical_script(statements: list[str]) -> tuple[list[str], list[bool]]:
    """Apply :func:`split_vertical` to every statement of a split script."""
    sql: list[str] = []
    vertical: list[bool] = []
    for statement in statements:
        for piece, is_vertical in split_vertical(statement):
            sql.append(piece)
            vertical.append(is_vertical)
    return sql, vertical
