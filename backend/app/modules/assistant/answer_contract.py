"""Deterministic checks for numeric claims made from tabular tool evidence.

The check reads numbers, never words, so it works in any language. Numbers are
found with CLDR locale data (separators, "juta", "Mio.", "万"). What a number
means (a cell, a change, a count, a list position, a comparison) comes from the
claims the model states with its answer, and every claim is checked against the
result cells. A number with no claim must still match a cell, simple arithmetic
over cells, a count of the result, or a number in the question.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from app.modules.assistant.locale_numbers import (
    UNIT_POWERS,
    NumberToken,
    display_matches,
    format_number,
    format_percent,
    number_tokens,
    with_scale,
)
from app.modules.assistant.messages import say

#: ISO dates and clock times in result labels are data, not claims.
_DATE = re.compile(
    r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"
)
_CLAIMS_BLOCK = re.compile(r"\s*<claims>(.*?)</claims>\s*$", re.S)
_SPACES = re.compile(r"\s+", re.UNICODE)

#: Schema identifier vocabularies (column names), not the user's language.
_PERCENT_IDENTIFIERS = frozenset({"pct", "percent", "percentage", "rate", "ratio", "margin"})
#: Unit suffixes that do not tell two percent columns apart (margin_pct vs growth_pct).
_UNIT_SUFFIX_IDENTIFIERS = frozenset({"pct", "percent", "percentage", "rate", "ratio"})
_COUNT_IDENTIFIERS = frozenset({"count", "quantity", "units", "qty"})
_MONEY_IDENTIFIERS = frozenset({
    "revenue", "amount", "profit", "cost", "price", "spend", "value", "total", "sales",
})

CLAIM_KINDS = ("cell", "derived", "count", "question", "position", "date", "comparison",
               "hypothesis")

CLAIMS_INSTRUCTION = (
    "After an answer that states numbers from data, add one final line: <claims> followed "
    "by a JSON array, then </claims>. List every number you wrote, in any language or "
    "format, as {\"text\": the number exactly as written with its % or unit word, "
    "\"value\": its plain numeric value (25.9 for 25,9%, 1200000 for 1,2 juta), "
    "\"kind\": cell | derived | count | question | position | date, \"evidence_id\", "
    "\"column\", \"row_label\" (the row it belongs to), \"direction\": up | down | null "
    "for a change, \"labels\": for a change [the row measured, the row it is compared "
    "with], for a share [the row]}. Also list "
    "each comparison you state (\"A is higher than B\", \"A is the highest\") as "
    "{\"text\": the phrase exactly as written, \"kind\": \"comparison\", \"relation\": "
    "greater | less | max | min, \"labels\": [A] or [A, B], \"column\"}. The block is "
    "removed before the user sees the answer. For each statement about a canonical "
    "Investigation hypothesis, also list {\"text\": the complete statement, "
    "\"kind\": \"hypothesis\", \"hypothesis_id\": the canonical id, "
    "\"causal_status\": its unchanged canonical arithmetic | association | "
    "supported_effect | unknown label}. Arithmetic and association do not establish "
    "a causal effect."
)


@dataclass(frozen=True)
class Claim:
    text: str
    kind: str | None = None
    value: Decimal | None = None
    evidence_id: str | None = None
    column: str | None = None
    row_label: str | None = None
    direction: str | None = None
    labels: tuple[str, ...] = ()
    relation: str | None = None
    hypothesis_id: str | None = None
    causal_status: str | None = None


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
    unsupported_hypotheses: tuple[str, ...] = ()


def split_claims(answer: str) -> tuple[str, tuple[Claim, ...] | None]:
    """The answer without its claims block, and the claims (None when absent)."""
    match = _CLAIMS_BLOCK.search(answer or "")
    if match is None:
        return answer, None
    text = answer[:match.start()].rstrip()
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError:
        return text, ()
    claims = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        value = item.get("value")
        number = None
        if isinstance(value, int | float | str) and not isinstance(value, bool):
            try:
                number = Decimal(str(value))
            except InvalidOperation:
                number = None
        labels = item.get("labels")
        claims.append(Claim(
            text=item["text"].strip()[:200],
            kind=item.get("kind") if item.get("kind") in CLAIM_KINDS else None,
            value=number,
            evidence_id=_text(item.get("evidence_id")),
            column=_text(item.get("column")),
            row_label=_text(item.get("row_label")),
            direction=item.get("direction") if item.get("direction") in {"up", "down"} else None,
            labels=tuple(str(label)[:80] for label in labels if isinstance(label, str))
            if isinstance(labels, list) else (),
            relation=item.get("relation")
            if item.get("relation") in {"greater", "less", "max", "min"} else None,
            hypothesis_id=_text(item.get("hypothesis_id")),
            causal_status=item.get("causal_status")
            if item.get("causal_status") in {"arithmetic", "association", "supported_effect",
                                             "unknown"} else None,
        ))
    return text, tuple(claims)


def _text(value: Any) -> str | None:
    return value.strip()[:120] if isinstance(value, str) and value.strip() else None


def _key(text: str) -> str:
    return _SPACES.sub("", str(text)).casefold()


# ── result cells ─────────────────────────────────────────────────────────────

def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | Decimal):
        # A numeric cell is already a number; separators only exist in text.
        try:
            return Decimal(str(value))
        except InvalidOperation:
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


def _percent_cell(value: Any) -> Decimal | None:
    """A cell written with a percent sign ("20.4763%"), in points."""
    raw = str(value or "").strip()
    if not raw.endswith("%"):
        return None
    return _number(raw[:-1])


def is_percent_column(column: str, table: dict[str, Any] | None = None) -> bool:
    units = (table or {}).get("units") or {}
    if str(units.get(column) or "").casefold() in {"percent", "%", "pct"}:
        return True
    return bool(set(str(column).casefold().split("_")) & _PERCENT_IDENTIFIERS)


def _row_labels(row: list[Any] | tuple[Any, ...]) -> frozenset[str]:
    return frozenset(
        str(cell).strip().casefold() for cell in row
        if isinstance(cell, str) and _number(cell) is None and _percent_cell(cell) is None
        and 1 < len(cell.strip()) <= 80
    )


@dataclass(frozen=True)
class _Cell:
    value: Decimal
    evidence_id: str
    column: str
    #: The value is a percentage (a percent column or a cell written with %).
    percent: bool
    labels: frozenset[str]


def _cells(tables: dict[str, dict[str, Any]]) -> list[_Cell]:
    cells = []
    for evidence_id, table in tables.items():
        columns = [str(col) for col in table.get("columns") or []]
        for row in (table.get("rows") or [])[:200]:
            if not isinstance(row, (list, tuple)):
                continue
            labels = _row_labels(row)
            for column, cell in zip(columns, row, strict=False):
                explicit = _percent_cell(cell)
                if explicit is not None:
                    cells.append(_Cell(explicit, evidence_id, column, True, labels))
                    continue
                parsed = _number(cell)
                if parsed is None:
                    continue
                percent = is_percent_column(column, table)
                cells.append(_Cell(parsed, evidence_id, column, percent, labels))
                if percent and abs(parsed) <= 1:
                    # A fraction column (0.3696) is stated as 36.96%.
                    cells.append(_Cell(parsed * 100, evidence_id, column, True, labels))
    return cells


def _table_counts(tables: dict[str, dict[str, Any]]) -> set[Decimal]:
    """Counts a reader sees in a result: its rows, the distinct values of a label
    column ("5 kota"), and the rows per value ("2 kategori per kota")."""
    counts: set[Decimal] = set()
    for table in tables.values():
        rows = table.get("rows") or []
        counts.add(Decimal(len(rows)))
        for index in range(len(table.get("columns") or [])):
            labels = [
                str(row[index]) for row in rows[:200]
                if index < len(row) and isinstance(row[index], str)
                and _number(row[index]) is None
            ]
            if not labels:
                continue
            per_value = Counter(labels)
            counts.add(Decimal(len(per_value)))
            counts.update(Decimal(size) for size in per_value.values())
    return counts


def _mask_dates(answer: str) -> str:
    """Blank ISO dates and clock times; positions are preserved."""
    return _DATE.sub(lambda match: " " * len(match.group(0)), answer)


def _table_years(tables: dict[str, dict[str, Any]]) -> set[Decimal]:
    """Years that appear in result labels ("2026-08-01", "2025-Q2") are data, not claims."""
    years: set[Decimal] = set()
    for table in tables.values():
        for row in (table.get("rows") or [])[:200]:
            for cell in row if isinstance(row, (list, tuple)) else []:
                if isinstance(cell, str):
                    years.update(Decimal(item) for item in re.findall(r"(?:19|20)\d{2}", cell))
    return years


# ── arithmetic over cells ────────────────────────────────────────────────────

_DERIVE_ROWS = 24
_DERIVE_COLUMNS = 8


@dataclass(frozen=True)
class _Derived:
    value: Decimal
    evidence_id: str
    column: str
    percent: bool
    directional: bool
    #: Row labels shared by every row the value is computed from.
    labels: frozenset[str] = frozenset()
    #: For a change between two rows: the labels of either row.
    pair_labels: frozenset[str] = frozenset()
    #: For a change: the row it is measured on (after) and against (before).
    subject: frozenset[str] = frozenset()
    reference: frozenset[str] = frozenset()


def _derivations(tables: dict[str, dict[str, Any]]) -> list[_Derived]:
    """Values a reader may state that are simple arithmetic over result cells.

    Changes run from an earlier row to a later one; rows labelled
    ``previous_period`` and ``current_period`` go from previous to current.
    """
    derived: list[_Derived] = []
    with localcontext() as context:
        context.prec = 34
        for evidence_id, table in tables.items():
            columns = [str(col) for col in table.get("columns") or []]
            rows = [
                row for row in (table.get("rows") or [])[:_DERIVE_ROWS]
                if isinstance(row, (list, tuple))
            ]
            labels = [_row_labels(row) for row in rows]
            shared = frozenset.intersection(*labels) if labels else frozenset()
            numeric: dict[str, list[Decimal]] = {}
            for index, column in enumerate(columns[:_DERIVE_COLUMNS * 2]):
                values = [_number(row[index]) if len(row) > index else None for row in rows]
                if values and all(value is not None for value in values):
                    numeric[column] = [value for value in values if value is not None]
            numeric = dict(list(numeric.items())[:_DERIVE_COLUMNS])

            def add(value: Decimal, column: str, percent: bool, directional: bool,
                    row_labels: frozenset[str], *, _id: str = evidence_id,
                    either: frozenset[str] = frozenset(),
                    subject: frozenset[str] = frozenset(),
                    reference: frozenset[str] = frozenset()) -> None:
                derived.append(_Derived(
                    value, _id, column, percent, directional, row_labels, either,
                    subject, reference,
                ))

            for column, values in numeric.items():
                percent_column = is_percent_column(column, table)
                total = sum(values, Decimal(0))
                if len(values) > 1:
                    add(total, column, percent_column, False, shared)
                    add(total / len(values), column, percent_column, False, shared)
                for first in range(len(values)):
                    for second in range(first + 1, len(values)):
                        before, after = values[first], values[second]
                        reference, subject = labels[first], labels[second]
                        if (
                            "current_period" in labels[first]
                            and "previous_period" in labels[second]
                        ):
                            before, after = after, before
                            reference, subject = subject, reference
                        pair = labels[first] & labels[second]
                        either = labels[first] | labels[second]
                        ends = {"subject": subject, "reference": reference}
                        add(after - before, column, percent_column, True, pair, either=either,
                            **ends)
                        if before != 0 and not percent_column:
                            add((after - before) / abs(before) * 100, column, True, True, pair,
                                either=either, **ends)
                if total > 0 and not percent_column and all(value >= 0 for value in values):
                    for value, row_labels in zip(values, labels, strict=True):
                        add(value / total * 100, column, True, False, row_labels)
            names = list(numeric)
            for numerator in names:
                for denominator in names:
                    if numerator == denominator:
                        continue
                    for top, bottom, row_labels in zip(
                        numeric[numerator], numeric[denominator], labels, strict=True
                    ):
                        if bottom == 0:
                            continue
                        add(top / bottom, numerator, False, False, row_labels)
                        add(top / bottom * 100, numerator, True, False, row_labels)
    return derived


def _signed(token: NumberToken) -> bool:
    return token.core.lstrip()[:1] in {"-", "+", "\u2212"}


def _derived_match(
    derived: list[_Derived], token: NumberToken, *, percent: bool,
    direction: str | None, label: str | None, ends: tuple[str, ...] = (),
    change: bool = False,
) -> _Derived | None:
    for item in derived:
        if item.percent != percent:
            continue
        # Only a claimed change may be spoken of with either row's label.
        allowed = item.labels | (item.pair_labels if change else frozenset())
        if label is not None and label not in allowed:
            continue
        value = item.value
        if item.directional and len(ends) == 2:
            # A claimed change names [subject, reference]: subject minus reference.
            subject, reference = ends[0].casefold(), ends[1].casefold()
            if subject in item.reference and reference in item.subject:
                value = -value
            elif not (subject in item.subject and reference in item.reference):
                continue
        candidates = [value]
        if item.directional and not _signed(token):
            # An unsigned change is checked against its claimed direction; with no
            # claim the magnitude alone is accepted.
            if direction == "down" and value < 0:
                candidates = [-value]
            elif direction == "up" and value > 0:
                candidates = [value]
            elif direction is None:
                candidates = [abs(value)]
            else:
                continue
        if any(display_matches(candidate, token) for candidate in candidates):
            return item
    return None


# ── where a number sits in the prose ────────────────────────────────────────
# Row labels and column names are data and identifiers, not words of a language:
# the nearest label before a number, within its sentence, is the row it speaks
# of, whatever language surrounds it. Boundaries are punctuation, in any script.

_SENTENCE_END = re.compile(r"(?:[.;!?](?:\s+|$)|\n|[\u3002\uff1b\uff01\uff1f])")
_CLAUSE_END = re.compile(r"(?:[,;.!?](?:\s+|$)|\n|[\u3002\uff1b\uff01\uff1f\u3001\uff0c])")


def _mentions(text: str, name: str) -> list[re.Match[str]]:
    return list(re.finditer(
        r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", text, re.I
    ))


def _prose_label(answer: str, token: NumberToken, labels: set[str]) -> str | None:
    """The row label the prose attaches to a number, or None when it names none."""
    prefix = answer[:token.start]
    ends = list(_SENTENCE_END.finditer(prefix))
    nearby = prefix[ends[-1].end() if ends else 0:]
    previous = [
        (match.end(), len(label), label.casefold())
        for label in labels for match in _mentions(nearby, label)
    ]
    if previous:
        return max(previous)[2]
    clause_end = _CLAUSE_END.search(answer, token.end)
    following = answer[token.end:clause_end.start() if clause_end else len(answer)]
    named = {label.casefold() for label in labels if _mentions(following, label)}
    return next(iter(named)) if len(named) == 1 else None


def _prose_columns(answer: str, token: NumberToken, columns: set[str]) -> set[str] | None:
    """Columns the clause names ("recognized revenue 100"); empty when ambiguous."""
    prefix = answer[:token.start]
    ends = list(_CLAUSE_END.finditer(prefix))
    clause = prefix[ends[-1].end() if ends else 0:]
    exact = {
        column: max(match.end() for match in found)
        for column in columns
        if (found := _mentions(clause, column.replace("_", " ")))
    }
    if not exact:
        exact = {
            column: max(match.end() for match in found)
            for column in columns
            for part in column.casefold().split("_")
            if len(part) >= 4 and part not in _UNIT_SUFFIX_IDENTIFIERS
            if (found := _mentions(clause, part))
        }
    if not exact:
        return None
    closest = max(exact.values())
    winners = {column for column, position in exact.items() if position == closest}
    return winners if len(winners) == 1 else set()


# ── the check ────────────────────────────────────────────────────────────────

def _cell_matches(value: Decimal, token: NumberToken, percent: bool) -> bool:
    """A plain number states a cell exactly; percentages and scaled amounts may round."""
    if token.percent or token.scale is not None or percent:
        return display_matches(value, token)
    return value in token.values


def _list_marker(answer: str, token: NumberToken) -> bool:
    """"1." or "2)" at the start of a line: a position in a list."""
    line_start = answer.rfind("\n", 0, token.start) + 1
    return (
        not answer[line_start:token.start].strip(" *-#>")
        and answer[token.end:token.end + 1] in {".", ")"}
    )


def check_numeric_answer(
    answer: str,
    *,
    question: str,
    tables: dict[str, dict[str, Any]],
    claims: tuple[Claim, ...] | None = None,
    language: str = "en",
) -> AnswerCheck:
    """Require every number in the answer to be supported by the result.

    A number the model claimed is checked as claimed: a cell (with its column and
    row when named), a derivation (with its direction), a count, a number from the
    question, a list position, or a date. A number with no claim must match any
    of these by value. Nothing reads the answer's words.
    """
    if not tables:
        return AnswerCheck(accepted=True, claims=(), unsupported=())
    # Several numbers can be written the same way ("1" twice): claims queue per text,
    # in answer order.
    by_text: dict[str, list[Claim]] = {}
    for item in claims or ():
        if item.kind not in {"comparison", "hypothesis"}:
            by_text.setdefault(_key(item.text), []).append(item)
    question_values = {
        value * (token.scale or 1)
        for token in number_tokens(question, language) for value in token.values
    }
    years = _table_years(tables) | {value for value in question_values if 1900 <= value <= 2100}
    cells = _cells(tables)
    row_labels = {label for item in cells for label in item.labels}
    table_labels = {
        str(cell).strip() for table in tables.values() for row in (table.get("rows") or [])[:200]
        if isinstance(row, (list, tuple))
        for cell in row if isinstance(cell, str) and str(cell).strip().casefold() in row_labels
    }
    columns = {item.column for item in cells}
    derived: list[_Derived] | None = None
    counts: set[Decimal] | None = None
    found: list[NumericClaim] = []
    unsupported: list[str] = []
    for token in number_tokens(_mask_dates(answer), language):
        queue = by_text.get(_key(token.text)) or by_text.get(_key(token.core)) or []
        spoken_first = _prose_label(answer, token, table_labels) if len(queue) > 1 else None
        claim = next(
            (item for item in queue if spoken_first and item.row_label
             and item.row_label.casefold() == spoken_first),
            queue[0] if queue else None,
        )
        if claim is not None:
            queue.remove(claim)
        if claim is None and token.scale is None:
            # A unit word the locale data does not know ("تريليون"): the claim states
            # the value, accepted only as the written number times a power of ten.
            token, claim = _claimed_unit(answer, token, claims or ())
        if claim is not None and claim.value is not None and not display_matches(
            claim.value, token
        ):
            unsupported.append(token.text)  # the claim names another number than the text
            continue
        kind = claim.kind if claim else None
        integers = {value for value in token.values if value == value.to_integral_value()}
        if kind in {None, "position"} and _list_marker(answer, token) and any(
            1 <= value <= 20 for value in integers
        ):
            found.append(NumericClaim(token.text, None, None))
            continue
        if kind == "date" and any(1 <= value <= 31 or value in years for value in integers):
            found.append(NumericClaim(token.text, None, None))
            continue
        # A number that only repeats the question is not a result, unless claimed so;
        # a small whole number from it ("top 5", "7 days") needs no claim.
        if (
            kind == "question"
            or (kind is None and not token.percent and token.scale is None and any(
                1 <= value <= 20 for value in integers
            ))
        ) and any(value * (token.scale or 1) in question_values for value in token.values):
            found.append(NumericClaim(token.text, None, None))
            continue
        if kind in {None, "count"} and not token.percent and token.scale is None:
            if counts is None:
                counts = _table_counts(tables)
            if integers & counts:
                found.append(NumericClaim(token.text, None, "row_count"))
                continue
        if kind not in {None, "cell", "derived"}:
            unsupported.append(token.text)
            continue
        claimed = claim.row_label.casefold() if claim and claim.row_label else None
        if claimed is not None and claimed not in row_labels:
            claimed = None  # a translated or unknown name binds nothing
        spoken = _prose_label(answer, token, table_labels)
        if claimed is not None and spoken is not None and claimed != spoken:
            unsupported.append(token.text)  # the claim and the prose name different rows
            continue
        label = claimed or spoken
        named_columns = (
            {claim.column} if claim and claim.column in columns
            else _prose_columns(answer, token, columns)
        )
        cell = None
        # "fell by 20" states a negative change cell (-20) by its size, when claimed.
        magnitude = claim is not None and claim.direction == "down" and not _signed(token)
        cell = None if kind == "derived" else next((
            item for item in cells
            if (claim is None or claim.evidence_id in {None, item.evidence_id})
            and (named_columns is None or item.column in named_columns)
            and (label is None or label in item.labels)
            and (item.percent or not token.percent)
            and _cell_matches(
                -item.value if magnitude and item.value < 0 else item.value, token, item.percent
            )
        ), None)
        if cell is not None:
            found.append(NumericClaim(token.text, cell.evidence_id, cell.column))
            continue
        if kind != "cell":
            if derived is None:
                derived = _derivations(tables)
            match = _derived_match(
                derived, token, percent=token.percent,
                direction=claim.direction if claim else None, label=label,
                ends=claim.labels if claim else (),
                # A percentage beside one row's label is that row's change; a plain
                # number needs the change claimed.
                change=token.percent or (
                    claim is not None and (claim.kind == "derived" or bool(claim.direction))
                ),
            )
            if match is not None:
                found.append(NumericClaim(token.text, match.evidence_id, match.column))
                continue
        if kind is None and token.scale is None and integers & years:
            found.append(NumericClaim(token.text, None, None))
            continue
        unsupported.append(token.text)
    return AnswerCheck(
        accepted=not unsupported,
        claims=tuple(found),
        unsupported=tuple(dict.fromkeys(unsupported)),
    )


def _claimed_unit(
    answer: str, token: NumberToken, claims: tuple[Claim, ...]
) -> tuple[NumberToken, Claim | None]:
    """Read "٣٫٣٧ تريليون" with a claim whose value is the number times 10^k."""
    following = answer[token.start:token.start + 40]
    for claim in claims:
        if claim.value is None or claim.kind == "comparison":
            continue
        written = _key(claim.text)
        if not written.startswith(_key(token.text)) or not _key(following).startswith(written):
            continue
        for power in UNIT_POWERS:
            scaled = with_scale(token, power, claim.text)
            if display_matches(claim.value, scaled):
                return scaled, claim
    return token, None


# ── comparisons ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ComparisonError:
    claim: str
    correction: str


def _label_values(
    tables: dict[str, dict[str, Any]], column: str | None = None
) -> list[dict[str, Decimal]]:
    """Per table: label -> the row's value in ``column`` (or its first number)."""
    output = []
    for table in tables.values():
        columns = [str(col) for col in table.get("columns") or []]
        index = columns.index(column) if column in columns else None
        values: dict[str, Decimal] = {}
        for row in (table.get("rows") or [])[:200]:
            if not isinstance(row, (list, tuple)):
                continue
            label = next(
                (str(cell).strip() for cell in row
                 if isinstance(cell, str) and _number(cell) is None and cell.strip()),
                None,
            )
            number = (
                _number(row[index]) if index is not None and index < len(row)
                else next((_number(cell) for cell in row if _number(cell) is not None), None)
            )
            if label and number is not None and label.casefold() not in values:
                values[label.casefold()] = number
        if len(values) >= 2:
            output.append(values)
    return output


def _places(value: Decimal) -> int:
    return 0 if value == value.to_integral_value() else min(2, -value.as_tuple().exponent)


def check_comparisons(
    answer: str,
    tables: dict[str, dict[str, Any]],
    claims: tuple[Claim, ...] | None = None,
    language: str = "en",
) -> list[ComparisonError]:
    """Stated comparisons ("A higher than B", "A is the highest") against result cells."""
    errors: list[ComparisonError] = []
    for claim in claims or ():
        if claim.kind != "comparison" or not claim.relation or not claim.labels:
            continue
        names = [label.casefold() for label in claim.labels]
        for values in _label_values(tables, claim.column):
            if not all(name in values for name in names):
                continue
            first = names[0]
            if claim.relation in {"greater", "less"} and len(names) >= 2:
                second = names[1]
                holds = (
                    values[first] > values[second] if claim.relation == "greater"
                    else values[first] < values[second]
                )
                if not holds and values[first] != values[second]:
                    winner, loser = (
                        (first, second) if values[first] > values[second] else (second, first)
                    )
                    errors.append(ComparisonError(
                        claim.text,
                        f"{_display_label(answer, winner)} "
                        f"({format_number(values[winner], language, _places(values[winner]))}) > "
                        f"{_display_label(answer, loser)} "
                        f"({format_number(values[loser], language, _places(values[loser]))})",
                    ))
            elif claim.relation in {"max", "min"}:
                top = claim.relation == "max"
                best = max(values, key=values.get) if top else min(values, key=values.get)
                if values[first] != values[best]:
                    errors.append(ComparisonError(claim.text, say(
                        "verify.is_highest" if top else "verify.is_lowest", language,
                        label=_display_label(answer, best),
                        value=format_number(values[best], language, _places(values[best])),
                    )))
            break
    return errors


def _display_label(answer: str, label: str) -> str:
    match = re.search(rf"(?<!\w){re.escape(label)}(?!\w)", answer, re.I)
    return match.group(0) if match else label


# ── the answer shown ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VerifiedAnswer:
    text: str
    check: AnswerCheck
    #: True when the drafted prose was replaced by a rendering from result cells.
    replaced: bool
    comparison: bool


#: A draft keeps its prose when at most this many numbers are unsupported and
#: at least one number was verified; the unsupported ones are removed in place.
_MAX_ANNOTATED = 2


def annotate_unverified(answer: str, unsupported: tuple[str, ...], *, language: str) -> str:
    """Keep the verified prose; replace only numbers no result cell supports."""
    marker = say("verify.unverified", language)
    for literal in unsupported:
        answer = re.sub(
            r"(?<![A-Za-z0-9.,])" + re.escape(literal) + r"(?![A-Za-z0-9]|[.,]\d)",
            marker, answer,
        )
    return answer.rstrip() + "\n\n" + say("verify.removed_note", language)


def _has_label_and_measure(tables: dict[str, dict[str, Any]]) -> bool:
    for table in tables.values():
        rows = table.get("rows") or []
        if len(rows) < 2:
            continue
        width = len(table.get("columns") or [])
        columns = [
            [_number(row[index]) if isinstance(row, (list, tuple)) and len(row) > index else None
             for row in rows]
            for index in range(width)
        ]
        if any(all(value is not None for value in column) for column in columns) and any(
            all(value is None for value in column) for column in columns
        ):
            return True
    return False


def finalize_verified_answer(
    answer: str,
    *,
    question: str,
    tables: dict[str, dict[str, Any]],
    tables_shown: bool = False,
    claims: tuple[Claim, ...] | None = None,
    language: str = "en",
    compares_groups: bool = False,
    canonical_observations: tuple[dict[str, Any], ...] = (),
) -> VerifiedAnswer:
    """The answer shown to the user: verified prose, annotated prose, or a rendering.

    * A comparison answered with no number: the comparison rendered from cells.
    * Every number verified: the draft is kept. A comparison also gets the
      highest/lowest lines computed from cells.
    * A few numbers unsupported: only those numbers are removed.
    * Otherwise: the result rendered from cells, in the user's language.
      ``tables_shown`` means the tables are already on screen, so the rendering
      is sentences only.
    """
    check = check_numeric_answer(
        answer, question=question, tables=tables, claims=claims, language=language
    )
    comparison = compares_groups and _has_label_and_measure(tables)
    known_hypotheses = {
        hypothesis.get("id"): hypothesis.get("causal_status")
        for observation in canonical_observations
        for hypothesis in observation.get("hypotheses", [])
    }
    unsupported_hypotheses = tuple(
        claim.text for claim in claims or () if claim.kind == "hypothesis"
        and (claim.hypothesis_id not in known_hypotheses
             or known_hypotheses[claim.hypothesis_id] != claim.causal_status)
    )
    if unsupported_hypotheses:
        replacement, _ = render_with_claims(
            tables, language=language, include_table=not tables_shown
        )
        return VerifiedAnswer(replacement, AnswerCheck(
            accepted=False, claims=check.claims,
            unsupported=check.unsupported, unsupported_hypotheses=unsupported_hypotheses,
        ), True, comparison)
    mistakes = check_comparisons(answer, tables, claims, language)
    if mistakes:
        # A wrong "higher than" or "highest" is replaced in place and corrected
        # from result cells, even when every number in the draft is right.
        for mistake in mistakes:
            answer = answer.replace(mistake.claim, say("verify.corrected", language))
        answer = answer.rstrip() + "\n\n" + say("verify.correction_heading", language) + "\n" + (
            "\n".join(f"- {mistake.correction}" for mistake in mistakes)
        )
        check = check_numeric_answer(
            answer, question=question, tables=tables, claims=claims, language=language
        )
    if check.accepted and comparison and not check.claims:
        # A comparison with no verified number is only unverifiable claims
        # ("X leads every metric"), so the comparison is shown from cells.
        rendered, rendered_claims = render_with_claims(
            tables, language=language, include_table=not tables_shown
        )
        if check_numeric_answer(
            rendered, question=question, tables=tables, claims=rendered_claims, language=language
        ).accepted:
            return VerifiedAnswer(rendered, check, True, comparison)
    if check.accepted:
        leaders = verified_leaders(tables, language=language) if comparison else []
        text = answer.rstrip() + ("\n\n" + "\n".join(leaders) if leaders else "")
        return VerifiedAnswer(text, check, False, comparison)
    data_claims = [claim for claim in check.claims if claim.evidence_id]
    tokens = number_tokens(_mask_dates(answer), language)
    headline = tokens[0].text if tokens else None
    if (
        data_claims
        and len(check.unsupported) <= max(_MAX_ANNOTATED, len(data_claims))
        and headline not in check.unsupported
    ):
        # Mostly verified prose stays; only the unsupported numbers are removed.
        # A wrong headline number is not patched around: the answer is rebuilt.
        text = annotate_unverified(answer, check.unsupported, language=language)
        return VerifiedAnswer(text, check, False, comparison)
    replacement, replacement_claims = render_with_claims(
        tables, language=language, include_table=not tables_shown
    )
    if check_numeric_answer(
        replacement, question=question, tables=tables, claims=replacement_claims,
        language=language,
    ).accepted:
        return VerifiedAnswer(replacement, check, True, comparison)
    return VerifiedAnswer(say("verify.cannot_verify", language), check, True, comparison)


# ── rendering from cells ─────────────────────────────────────────────────────

@dataclass
class _Rendering:
    """Rendered text plus a claim for every number it writes."""

    language: str
    claims: list[Claim] = field(default_factory=list)

    def number(self, value: Decimal, places: int, **claim: Any) -> str:
        text = format_number(value, self.language, places)
        self.claims.append(Claim(text=text, value=value, **claim))
        return text

    def percent(self, value: Decimal, places: int = 2, **claim: Any) -> str:
        text = format_percent(value, self.language, places)
        self.claims.append(Claim(text=text, value=value, **claim))
        return text


def _layout(table: dict[str, Any]) -> tuple[list[str], list[list[Any]], list[int], list[int]]:
    columns = [str(value)[:80] for value in (table.get("columns") or [])[:12]]
    rows = [
        list(row[:len(columns)])
        for row in (table.get("rows") or [])[:20]
        if isinstance(row, (list, tuple)) and len(row) >= len(columns)
    ]
    labels = [
        index for index in range(len(columns))
        if rows and all(_number(row[index]) is None for row in rows)
    ]
    measures = [
        index for index in range(len(columns))
        if index not in labels and columns[index].casefold() != "rank"
        and rows and all(_number(row[index]) is not None for row in rows)
    ][:3]
    return columns, rows, labels, measures


def _period_name(value: Any, language: str) -> str:
    text = str(value)
    if text in {"current_period", "previous_period"}:
        return say(f"render.{text}", language)
    return text


def _value_text(
    rendering: _Rendering, evidence_id: str, table: dict[str, Any], column: str,
    value: Any, row_label: str | None,
) -> str:
    parsed = _number(value)
    if parsed is None:
        return str(value if value is not None else "")
    source = {"kind": "cell", "evidence_id": evidence_id, "column": column,
              "row_label": row_label}
    words = set(column.casefold().split("_"))
    if is_percent_column(column, table):
        points = parsed if abs(parsed) > 1 else parsed * 100
        return rendering.percent(points, 2, **source)
    if parsed == parsed.to_integral_value():
        return rendering.number(parsed, 0, **source)
    exponent = max(0, -parsed.as_tuple().exponent)
    if words & _MONEY_IDENTIFIERS and exponent <= 2:
        return rendering.number(parsed, 2, **source)
    return rendering.number(parsed, exponent, **source)


def render_verified_comparison(
    tables: dict[str, dict[str, Any]], *, language: str = "en", include_table: bool = True
) -> str:
    return render_with_claims(tables, language=language, include_table=include_table)[0]


def verified_leaders(tables: dict[str, dict[str, Any]], *, language: str = "en") -> list[str]:
    """Highest/lowest lines computed from result cells, in the user's language."""
    rendering = _Rendering(language)
    lines = []
    for evidence_id, table in tables.items():
        columns, rows, labels, measures = _layout(table)
        if len(rows) < 2 or not labels:
            continue
        for index in measures:
            ordered = sorted(rows, key=lambda row, i=index: _number(row[i]), reverse=True)

            def describe(
                row: list[Any], i: int = index, source: str = evidence_id,
                data: dict[str, Any] = table, names: list[str] = columns,
                positions: list[int] = labels,
            ) -> str:
                label = " / ".join(_period_name(row[p], language) for p in positions)
                value = _value_text(
                    rendering, source, data, names[i], row[i], str(row[positions[0]])
                )
                return f"{label} ({value})"

            lines.append(f"{columns[index].replace('_', ' ')}: " + say(
                "render.extremes", language, high=describe(ordered[0]), low=describe(ordered[-1]),
            ) + ".")
    return lines


def render_with_claims(
    tables: dict[str, dict[str, Any]], *, language: str = "en", include_table: bool = True
) -> tuple[str, tuple[Claim, ...]]:
    """The result in sentences (and optionally tables), with a claim per number."""
    rendering = _Rendering(language)
    sections: list[str] = []
    for evidence_id, table in tables.items():
        columns, rows, labels, measures = _layout(table)
        if not columns:
            continue
        sentences = _sentences(rendering, evidence_id, table, columns, rows, labels, measures)
        lines = [
            "| " + " | ".join(column.replace("|", "\\|") for column in columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
        ]
        if include_table:
            lines.extend(
                "| " + " | ".join(
                    _value_text(
                        rendering, evidence_id, table, column, value,
                        str(row[labels[0]]) if labels else None,
                    ).replace("|", "\\|").replace("\n", " ")
                    for value, column in zip(row, columns, strict=True)
                ) + " |"
                for row in rows
            )
        if (
            table.get("truncated")
            or len(table.get("rows") or []) > len(rows)
            or len(table.get("columns") or []) > len(columns)
        ):
            lines.extend(("", say("render.omitted", language)))
        elif not rows:
            lines.extend(("", say("render.no_rows", language)))
        if include_table:
            sections.append("\n".join(sentences + ["\n".join(lines)]))
        elif sentences:
            sections.append("\n".join(sentences))
    if not sections and include_table:
        return say("render.no_result", language), tuple(rendering.claims)
    if not include_table:
        # The tables are already on screen; say what they contain in sentences.
        lead = say("render.lead", language)
        return lead + ("\n\n" + "\n".join(sections) if sections else ""), tuple(rendering.claims)
    return (
        say("render.heading", language) + "\n\n" + "\n\n".join(sections),
        tuple(rendering.claims),
    )


def _sentences(
    rendering: _Rendering, evidence_id: str, table: dict[str, Any], columns: list[str],
    rows: list[list[Any]], labels: list[int], measures: list[int],
) -> list[str]:
    language = rendering.language
    if not rows or not measures:
        return []
    name = {index: columns[index].replace("_", " ") for index in measures}

    def cell(row: list[Any], index: int) -> str:
        row_label = str(row[labels[0]]) if labels else None
        return _value_text(rendering, evidence_id, table, columns[index], row[index], row_label)

    def label_of(row: list[Any]) -> str:
        return " / ".join(_period_name(row[position], language) for position in labels)

    if not labels:
        if len(rows) != 1:
            return []
        return ["; ".join(f"{name[i]}: {cell(rows[0], i)}" for i in measures) + "."]
    if len(rows) == 1:
        return [label_of(rows[0]) + ": " + ", ".join(
            f"{name[i]} {cell(rows[0], i)}" for i in measures
        ) + "."]
    periods = {str(row[labels[0]]): row for row in rows}
    if len(rows) == 2 and set(periods) == {"current_period", "previous_period"}:
        current, previous = periods["current_period"], periods["previous_period"]
        out = []
        for i in measures:
            after, before = _number(current[i]), _number(previous[i])
            line = (
                f"{name[i]}: {_period_name('current_period', language)} {cell(current, i)}, "
                f"{_period_name('previous_period', language)} {cell(previous, i)}"
            )
            if after is not None and before is not None:
                delta = after - before
                if delta == 0:
                    line += f" ({say('render.unchanged', language)})"
                else:
                    direction = "up" if delta > 0 else "down"
                    change = {
                        "kind": "derived", "evidence_id": evidence_id, "column": columns[i],
                        "direction": direction,
                    }
                    places = 0 if delta == delta.to_integral_value() else 2
                    amount = rendering.number(abs(delta), places, **change)
                    if before != 0:
                        with localcontext() as context:
                            context.prec = 34
                            share = abs(delta) / abs(before) * 100
                        amount += "; " + rendering.percent(share, 2, **change)
                    line += " (" + say(f"render.{direction}", language, amount=amount) + ")"
            out.append(line + ".")
        return out
    out = []
    for i in measures:
        ordered = sorted(rows, key=lambda row, i=i: _number(row[i]), reverse=True)
        if len(rows) <= 6:
            out.append(f"{name[i]}: " + ", ".join(
                f"{label_of(row)} {cell(row, i)}" for row in ordered
            ) + ".")
            continue
        high, low = ordered[0], ordered[-1]
        out.append(f"{name[i]}: " + say(
            "render.extremes", language,
            high=f"{label_of(high)} ({cell(high, i)})", low=f"{label_of(low)} ({cell(low, i)})",
        ) + ".")
    return out
