"""Deterministic checks for numeric claims made from tabular tool evidence."""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER = re.compile(
    r"(?<![\w.,])(?:Rp|IDR|USD|[$€£])?\s*([-+]?\d+(?:[.,]\d+)*(?:\s*%)?)(?![\w])",
    re.I,
)
_PERCENT_COLUMN = re.compile(r"(?:percent|percentage|pct|rate|growth|margin)", re.I)
_DECREASE = re.compile(
    r"\b(?:fell|dropped|declined|decreased|turun|menurun|berkurang)\s+"
    r"(?:by|sebesar|sebanyak)?\s*$",
    re.I,
)


@dataclass(frozen=True)
class NumericClaim:
    text: str
    evidence_id: str | None
    column: str | None


@dataclass(frozen=True)
class AnswerCheck:
    accepted: bool
    claims: tuple[NumericClaim, ...]
    unsupported: tuple[str, ...]


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    raw = str(value).strip().replace(" ", "")
    if not raw or not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)*", raw):
        return None
    if "," in raw and "." in raw:
        raw = (
            raw.replace(",", "")
            if raw.rfind(".") > raw.rfind(",")
            else raw.replace(".", "").replace(",", ".")
        )
    elif raw.count(",") > 1 or raw.count(".") > 1:
        mark = "," if "," in raw else "."
        raw = raw.replace(mark, "")
    elif "," in raw:
        left, right = raw.split(",")
        raw = (
            left + right
            if len(right) == 3 and len(left.lstrip("+-")) <= 3
            else left + "." + right
        )
    elif "." in raw:
        left, right = raw.split(".")
        raw = left + right if len(right) == 3 and len(left.lstrip("+-")) <= 3 else raw
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _candidates(value: Any) -> set[Decimal]:
    parsed = _number(value)
    if parsed is None:
        return set()
    values = {parsed}
    raw = str(value).strip()
    if re.fullmatch(r"[-+]?\d+[.,]\d{3}", raw):
        with suppress(InvalidOperation):
            values.add(Decimal(raw.replace(",", ".")))
    return values


def _claim_segment(answer: str, start: int, end: int) -> str:
    boundaries = list(re.finditer(r"(?:[,;.!?]\s+|\n|\b(?:and|dan)\b\s+)", answer, re.I))
    left = max((item.end() for item in boundaries if item.end() <= start), default=0)
    right = min((item.start() for item in boundaries if item.start() >= end), default=len(answer))
    return answer[left:right]


def _claim_prefix(answer: str, start: int) -> str:
    boundaries = list(re.finditer(r"(?:[,;.!?]\s+|\n|\b(?:and|dan)\b\s+)", answer[:start], re.I))
    return answer[boundaries[-1].end() if boundaries else 0:start]


def _input_context(answer: str, match: re.Match[str], value: Decimal) -> bool:
    if value == value.to_integral_value() and 1900 <= value <= 2100:
        return True
    prefix = answer[max(0, match.start() - 16):match.start()].lower()
    return bool(re.search(r"\b(?:top|limit|teratas)\s*$", prefix))


def _claimed_columns(segment: str, columns: set[str]) -> set[str] | None:
    """Resolve an explicit column mention; an ambiguous mention matches nothing."""
    exact = {
        column: max(match.end() for match in re.finditer(
            r"(?<!\w)" + re.escape(column.replace("_", " ")) + r"(?!\w)",
            segment, re.I,
        ))
        for column in columns
        if re.search(
            r"(?<!\w)" + re.escape(column.replace("_", " ")) + r"(?!\w)",
            segment, re.I,
        )
    }
    if exact:
        closest = max(exact.values())
        winners = {column for column, position in exact.items() if position == closest}
        return winners if len(winners) == 1 else set()
    partial: dict[str, int] = {}
    for column in columns:
        for token in column.casefold().split("_"):
            if len(token) < 4 or token in {"percent", "percentage", "pct", "rate"}:
                continue
            terms = (token, "omzet") if token == "revenue" else (token,)
            for term in terms:
                for match in re.finditer(
                    r"(?<!\w)" + re.escape(term) + r"s?(?!\w)", segment, re.I
                ):
                    partial[column] = max(partial.get(column, -1), match.end())
    if partial:
        closest = max(partial.values())
        winners = {column for column, position in partial.items() if position == closest}
        return winners if len(winners) == 1 else set()
    return None


def check_numeric_answer(
    answer: str,
    *,
    question: str,
    tables: dict[str, dict[str, Any]],
) -> AnswerCheck:
    """Require each new numeric literal to occur in an authorized result cell.

    This is a conservative boundary for data answers, not a semantic-equivalence
    proof. A derived percentage must be returned by SQL as a percentage column.
    Question literals remain allowed because they may be dates or filter values.
    """
    if not tables:
        return AnswerCheck(accepted=True, claims=(), unsupported=())
    input_values = {
        value
        for match in _NUMBER.finditer(question)
        for value in _candidates(match.group(1).rstrip("%"))
    }
    cells: list[tuple[Decimal, str, str, bool, tuple[str, ...]]] = []
    column_names: set[str] = set()
    for evidence_id, table in tables.items():
        columns = [str(col) for col in table.get("columns") or []]
        column_names.update(columns)
        for row in (table.get("rows") or [])[:200]:
            labels = tuple(
                str(cell).strip() for cell in row
                if isinstance(cell, str) and _number(cell) is None
                and 1 < len(cell.strip()) <= 80
            )
            for column, cell in zip(columns, row, strict=False):
                percent = bool(_PERCENT_COLUMN.search(column) or "%" in str(cell))
                for value in _candidates(str(cell).rstrip("%")):
                    cells.append((value, evidence_id, column, percent, labels))
    claims: list[NumericClaim] = []
    unsupported: list[str] = []
    for match in _NUMBER.finditer(answer):
        literal = match.group(1).strip()
        is_percent = literal.endswith("%")
        values = _candidates(literal.rstrip("% "))
        if not values:
            continue
        segment = _claim_segment(answer, match.start(), match.end())
        claimed_columns = _claimed_columns(_claim_prefix(answer, match.start()), column_names)
        prefix = answer[max(0, match.start() - 32):match.start()]
        negative_magnitude = not literal.startswith("-") and bool(_DECREASE.search(prefix))
        mentioned_labels = {
            label.casefold()
            for _, _, _, _, labels in cells for label in labels
            if re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", segment, re.I)
        }
        found = next(
            (
                (evidence_id, column)
                for value, evidence_id, column, percent, labels in cells
                if claimed_columns is None or column in claimed_columns
                if (
                    value in values
                    or (is_percent and value * 100 in values)
                    or (negative_magnitude and -value in values)
                )
                and (not is_percent or percent)
                and (not mentioned_labels or any(
                    label.casefold() in mentioned_labels for label in labels
                ))
            ),
            None,
        )
        if found:
            claims.append(NumericClaim(literal, found[0], found[1]))
        elif not is_percent and values & input_values and any(
            _input_context(answer, match, value) for value in values
        ):
            claims.append(NumericClaim(literal, None, None))
        else:
            unsupported.append(literal)
    return AnswerCheck(
        accepted=not unsupported,
        claims=tuple(claims),
        unsupported=tuple(dict.fromkeys(unsupported)),
    )
