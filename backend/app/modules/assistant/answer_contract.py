"""Deterministic checks for numeric claims made from tabular tool evidence."""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

_NUMBER = re.compile(
    r"(?<![\w.,])(?:Rp|IDR|USD|[$€£])?\s*"
    r"([-+]?\d+(?:[.,]\d+)*(?:\s*%)?)(?P<unit>[A-Za-z][A-Za-z0-9]*)?(?!\w|[.,]\d)",
    re.I,
)
_PERCENT_COLUMN = re.compile(r"(?:percent|percentage|pct|rate|growth|margin)", re.I)
_DECREASE = re.compile(
    r"\b(?:fell|dropped|declined|decreased|down|turun|menurun|berkurang)\s+"
    r"(?:by|sebesar|sebanyak)?\s*$",
    re.I,
)
_SCALES = {
    "ribu": Decimal(1_000),
    "thousand": Decimal(1_000),
    "juta": Decimal(1_000_000),
    "million": Decimal(1_000_000),
    "miliar": Decimal(1_000_000_000),
    "billion": Decimal(1_000_000_000),
    "triliun": Decimal(1_000_000_000_000),
    "trillion": Decimal(1_000_000_000_000),
    "k": Decimal(1_000),
    "m": Decimal(1_000_000),
    "mn": Decimal(1_000_000),
    "b": Decimal(1_000_000_000),
    "bn": Decimal(1_000_000_000),
    "t": Decimal(1_000_000_000_000),
    "tn": Decimal(1_000_000_000_000),
}
_INDONESIAN_SCALES = frozenset({"ribu", "juta", "miliar", "triliun"})
_ENGLISH_SCALES = frozenset({"thousand", "million", "billion", "trillion"})


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


_PERIOD_COUNT = re.compile(
    r"(?:\s*|-)(?:complete\s+|full\s+|terakhir\s+)?(?:days?|weeks?|months?|quarters?|years?|"
    r"hari|minggu|pekan|bulan|kuartal|triwulan|tahun)\b",
    re.I,
)
_CHANGE_WORDS = re.compile(
    r"\b(?:selisih|difference|change|changed|perubahan|dibanding(?:kan)?|vs|versus|"
    r"compared|growth|pertumbuhan|tumbuh|delta)\b",
    re.I,
)
_DATE = re.compile(
    r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"
)


# "26 persen", and the first bound of "24 sampai 26 persen" / "24-26%".
_PERCENT_WORD = re.compile(r"\s*(?:persen|percent|per\s+cent)\b", re.I)
_PERCENT_RANGE_END = re.compile(
    r"\s*(?:-|–|to|sampai|hingga|s/d|and|dan)\s*[-+]?\d+(?:[.,]\d+)?\s*"
    r"(?:%|persen\b|percent\b|per\s+cent\b)",
    re.I,
)


_ORDINAL_PREFIX = re.compile(
    r"(?:#|\b(?:rank|ranked|peringkat|urutan|nomor|no\.|top|posisi|position))\s*$", re.I
)


_MONTH_NAME = (
    r"(?:jan(?:uary|uari)?|feb(?:ruary|ruari)?|mar(?:ch|et)?|apr(?:il)?|may|mei|"
    r"jun(?:e|i)?|jul(?:y|i)?|aug(?:ust)?|agu(?:stus)?|agt|sep(?:tember)?|okt(?:ober)?|"
    r"oct(?:ober)?|nov(?:ember)?|des(?:ember)?|dec(?:ember)?)"
)
_DAY_BEFORE_MONTH = re.compile(rf"\s+{_MONTH_NAME}\b", re.I)
_MONTH_BEFORE_DAY = re.compile(rf"\b{_MONTH_NAME}\s+$", re.I)


def _calendar_day(answer: str, start: int, end: int, literal: str) -> bool:
    if not literal.isdigit() or not 1 <= int(literal) <= 31:
        return False
    return bool(
        _DAY_BEFORE_MONTH.match(answer, end)
        or _MONTH_BEFORE_DAY.search(answer[max(0, start - 12):start])
    )


def _ordinal(answer: str, start: int, end: int, literal: str) -> bool:
    if not literal.isdigit() or int(literal) > 20:
        return False
    if _ORDINAL_PREFIX.search(answer[max(0, start - 16):start]):
        return True
    line_start = answer.rfind("\n", 0, start) + 1
    # A numbered list marker: "1. Medan" or "2) Jakarta" at the start of a line.
    return (
        not answer[line_start:start].strip()
        and bool(re.match(r"[.)]\s", answer[end:end + 2]))
    )


_COUNTED_NOUN = re.compile(
    r"\s+(?!(?:times|kali|lipat|fold|x|points?|poin|basis|bps)\b)[A-Za-z][\w-]*", re.I
)


def _table_counts(tables: dict[str, dict[str, Any]]) -> set[Decimal]:
    """Counts a reader sees in a result: its rows, the distinct values of a label
    column ("5 kota"), and the rows per value ("2 kategori per kota")."""
    from collections import Counter

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
    """Blank dates and clock times so "2026-07-01" is not read as 2026, 07 and 01.

    Positions are preserved; the caller still reads context from ``answer``.
    """
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


def _percent_values(literal: str) -> set[Decimal]:
    raw = literal.rstrip("% ").replace(" ", "")
    if not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", raw):
        return set()
    try:
        return {Decimal(raw.replace(",", "."))}
    except InvalidOperation:
        return set()


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


def _display_matches(value: Decimal, shown: set[Decimal], literal: str) -> bool:
    """Match an explicitly rounded display value without a broad tolerance."""
    digits = literal.rstrip("% ").replace(" ", "")
    separator = "," if digits.rfind(",") > digits.rfind(".") else "."
    places = len(digits.rsplit(separator, 1)[-1]) if separator in digits else 0
    quantum = Decimal(1).scaleb(-places)
    with localcontext() as context:
        context.prec = max(context.prec, len(value.as_tuple().digits) + places + 4)
        return value.quantize(quantum, rounding=ROUND_HALF_UP) in shown


def _display_scale(answer: str, end: int, attached_unit: str | None) -> tuple[Decimal, str] | None:
    if attached_unit:
        unit = attached_unit.casefold()
        return (_SCALES[unit], unit) if unit in _SCALES else None
    suffix = answer[end:end + 24]
    match = re.match(
        r"\s*(ribu|thousand|juta|million|miliar|billion|triliun|trillion)\b",
        suffix,
        re.I,
    )
    if match is None:
        return None
    unit = match.group(1).casefold()
    return _SCALES[unit], unit


def _scaled_display(literal: str, unit: str) -> tuple[Decimal, int] | None:
    raw = literal.rstrip("% ").replace(" ", "")
    if not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)*", raw):
        return None
    comma_count, dot_count = raw.count(","), raw.count(".")
    if comma_count and dot_count:
        decimal_mark = "," if raw.rfind(",") > raw.rfind(".") else "."
    elif comma_count > 1 or dot_count > 1:
        decimal_mark = None
    elif comma_count or dot_count:
        mark = "," if comma_count else "."
        digits_after = len(raw.rsplit(mark, 1)[-1])
        if digits_after != 3:
            decimal_mark = mark
        elif unit in _INDONESIAN_SCALES:
            decimal_mark = "," if mark == "," else None
        elif unit in _ENGLISH_SCALES:
            decimal_mark = "." if mark == "." else None
        else:
            return None
    else:
        decimal_mark = None
    grouping_mark = next(
        (mark for mark in (",", ".") if mark != decimal_mark and mark in raw), None
    )
    integer = raw.split(decimal_mark, 1)[0] if decimal_mark else raw
    if grouping_mark:
        groups = integer.lstrip("+-").split(grouping_mark)
        if not (1 <= len(groups[0]) <= 3 and all(len(group) == 3 for group in groups[1:])):
            return None
    fraction = raw.rsplit(decimal_mark, 1)[-1] if decimal_mark else ""
    if grouping_mark and grouping_mark in fraction:
        return None
    normalized = raw.replace(grouping_mark, "") if grouping_mark else raw
    if decimal_mark == ",":
        normalized = normalized.replace(",", ".")
    try:
        return Decimal(normalized), len(fraction)
    except InvalidOperation:
        return None


def _scaled_matches(value: Decimal, scale: Decimal, display: tuple[Decimal, int]) -> bool:
    shown, places = display
    quantum = Decimal(1).scaleb(-places)
    with localcontext() as context:
        context.prec = max(context.prec, len(value.as_tuple().digits) + places + 4)
        return (value / scale).quantize(quantum, rounding=ROUND_HALF_UP) == shown


def _claimed_label(answer: str, start: int, end: int, labels: set[str]) -> str | None:
    prefix = answer[:start]
    hard_boundaries = list(re.finditer(r"(?:[.;!?]\s+|\n)", prefix))
    nearby = prefix[hard_boundaries[-1].end() if hard_boundaries else 0:]
    previous = [
        (match.end(), len(label), label.casefold())
        for label in labels
        for match in re.finditer(r"(?<!\w)" + re.escape(label) + r"(?!\w)", nearby, re.I)
    ]
    if previous:
        return max(previous)[2]
    segment = _claim_segment(answer, start, end)
    following = {
        label.casefold()
        for label in labels
        if re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", segment, re.I)
    }
    return next(iter(following)) if len(following) == 1 else None


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
    proof. A literal that is not a cell may still be simple arithmetic over cells
    of one result column (difference, percent change, share, total, average) or a
    ratio of two columns in one row; see ``_derivations``. Question literals remain
    allowed because they may be dates or filter values.
    """
    if not tables:
        return AnswerCheck(accepted=True, claims=(), unsupported=())
    input_values = {
        value
        for match in _NUMBER.finditer(question)
        for value in _candidates(match.group(1).rstrip("%"))
    }
    cells: list[tuple[Decimal, str, str, bool, bool, tuple[str, ...]]] = []
    column_names: set[str] = set()
    row_labels: set[str] = set()
    for evidence_id, table in tables.items():
        columns = [str(col) for col in table.get("columns") or []]
        for row in (table.get("rows") or [])[:200]:
            labels = tuple(
                str(cell).strip() for cell in row
                if isinstance(cell, str) and _number(cell) is None
                and 1 < len(cell.strip()) <= 80
            )
            row_labels.update(labels)
            for column, cell in zip(columns, row, strict=False):
                explicit_percent = "%" in str(cell)
                percent = bool(_PERCENT_COLUMN.search(column) or explicit_percent)
                cell_values = _percent_values(str(cell)) if percent else _candidates(str(cell))
                for value in cell_values:
                    percent_points = explicit_percent or abs(value) > 1
                    cells.append((value, evidence_id, column, percent, percent_points, labels))
                    # Only a column holding numbers can be what a number claims;
                    # "previous period" names a label column, not a value.
                    column_names.add(column)
    claims: list[NumericClaim] = []
    unsupported: list[str] = []
    derived: list[_Derived] | None = None
    counts: set[Decimal] | None = None
    input_values |= _table_years(tables)
    for match in _NUMBER.finditer(_mask_dates(answer)):
        literal = match.group(1).strip()
        # Context is read from the digits, not the whitespace the pattern may lead with.
        digits_start = match.start(1)
        is_percent = literal.endswith("%")
        if not is_percent and not match.group("unit") and (
            _PERCENT_WORD.match(answer, match.end())
            or _PERCENT_RANGE_END.match(answer, match.end())
        ):
            literal += "%"
            is_percent = True
        values = _percent_values(literal) if is_percent else _candidates(literal.rstrip("% "))
        if not values:
            unsupported.append(literal)
            continue
        if not is_percent and _PERIOD_COUNT.match(answer, match.end()):
            # "the last 3 months" describes the query window; it is not a result.
            continue
        if not is_percent and _calendar_day(answer, digits_start, match.end(), literal):
            # "1 Januari", "March 31": a date, not a result value.
            continue
        if not is_percent and _ordinal(answer, digits_start, match.end(), literal):
            # "#1", "peringkat 1", a numbered list item: a position, not a value.
            continue
        attached_unit = match.group("unit")
        scale = _display_scale(answer, match.end(), attached_unit)
        if attached_unit and scale is None:
            unsupported.append(literal + attached_unit)
            continue
        scaled_display = _scaled_display(literal, scale[1]) if scale else None
        claimed_label = _claimed_label(answer, match.start(), match.end(), row_labels)
        claimed_columns = _claimed_columns(_claim_prefix(answer, match.start()), column_names)
        prefix = answer[max(0, match.start() - 32):match.start()]
        negative_magnitude = not literal.startswith("-") and bool(_DECREASE.search(prefix))
        found = next(
            (
                (evidence_id, column)
                for value, evidence_id, column, percent, percent_points, labels in cells
                if claimed_columns is None or column in claimed_columns
                if (
                    (
                        scale is None
                        and (
                            (not is_percent and value in values)
                            or (
                                is_percent
                                and percent
                                and _display_matches(
                                    value if percent_points else value * 100,
                                    values,
                                    literal,
                                )
                            )
                            or (
                                negative_magnitude
                                and _display_matches(
                                    -(value if percent_points or not is_percent else value * 100),
                                    values,
                                    literal,
                                )
                            )
                        )
                    )
                    or (
                        scale is not None
                        and scaled_display is not None
                        and _scaled_matches(value, scale[0], scaled_display)
                    )
                )
                and (not is_percent or percent)
                and (claimed_label is None or any(
                    label.casefold() == claimed_label for label in labels
                ))
            ),
            None,
        )
        if found is None:
            if derived is None:
                derived = _derivations(tables)
            direction = _direction(_claim_prefix(answer, match.start()))
            segment = _claim_segment(answer, match.start(), match.end())
            found = _derived_match(
                derived, values, literal, is_percent=is_percent,
                scale=scale, scaled_display=scaled_display, direction=direction,
                claimed_label=claimed_label,
                change_claim=direction is not None or bool(_CHANGE_WORDS.search(segment)),
            )
        if found is None and scale is None and not is_percent and literal.isdigit() and (
            _COUNTED_NOUN.match(answer, match.end())
        ):
            # "5 kota", "10 rows": a count of the result, not a cell.
            if counts is None:
                counts = _table_counts(tables)
            if Decimal(literal) in counts:
                found = (None, "row_count")
        if found:
            claims.append(NumericClaim(literal, found[0], found[1]))
        elif scale is None and not is_percent and values & input_values and any(
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


#: Bounds that keep derivation deterministic and the false-match rate low.
_DERIVE_ROWS = 24
_DERIVE_COLUMNS = 8
_INCREASE = re.compile(
    r"\b(?:rose|grew|increased|increase|up|gained|growth|naik|tumbuh|meningkat|bertambah|"
    r"kenaikan|pertumbuhan)\b",
    re.I,
)
_DECREASE_WORD = re.compile(
    r"\b(?:fell|dropped|declined|decreased|decrease|down|lost|turun|menurun|berkurang|"
    r"penurunan|anjlok)\b",
    re.I,
)


@dataclass(frozen=True)
class _Derived:
    value: Decimal
    evidence_id: str
    column: str
    percent: bool
    directional: bool
    #: Row labels shared by every row the value is computed from. A claim bound
    #: to a label ("West: 100") may use only values that belong to that label.
    labels: frozenset[str] = frozenset()
    #: For a change between two rows: the labels of either row. A change claim
    #: ("Agustus turun 6%") names one end of the pair.
    pair_labels: frozenset[str] = frozenset()


def _direction(prefix: str) -> str | None:
    down = bool(_DECREASE_WORD.search(prefix))
    up = bool(_INCREASE.search(prefix))
    if down == up:
        return None
    return "down" if down else "up"


def _row_labels(row: list[Any] | tuple[Any, ...]) -> frozenset[str]:
    return frozenset(
        str(cell).strip().casefold() for cell in row
        if isinstance(cell, str) and _number(cell) is None and 1 < len(cell.strip()) <= 80
    )


def _derivations(tables: dict[str, dict[str, Any]]) -> list[_Derived]:
    """Values a reader may state that are simple arithmetic over result cells.

    Changes are computed in row order (earlier row to later row), because a
    result ordered by period is what gives "rose" or "fell" a direction. Rows
    labelled ``previous_period`` and ``current_period`` go from previous to current.
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
                    either: frozenset[str] = frozenset()) -> None:
                derived.append(
                    _Derived(value, _id, column, percent, directional, row_labels, either)
                )

            for column, values in numeric.items():
                percent_column = bool(_PERCENT_COLUMN.search(column))
                total = sum(values, Decimal(0))
                if len(values) > 1:
                    add(total, column, percent_column, False, shared)
                    add(total / len(values), column, percent_column, False, shared)
                for first in range(len(values)):
                    for second in range(first + 1, len(values)):
                        before, after = values[first], values[second]
                        if (
                            "current_period" in labels[first]
                            and "previous_period" in labels[second]
                        ):
                            before, after = after, before
                        pair = labels[first] & labels[second]
                        either = labels[first] | labels[second]
                        add(after - before, column, percent_column, True, pair, either=either)
                        if before != 0 and not percent_column:
                            add((after - before) / abs(before) * 100, column, True, True, pair,
                                either=either)
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


def _derived_match(
    derived: list[_Derived],
    values: set[Decimal],
    literal: str,
    *,
    is_percent: bool,
    scale: tuple[Decimal, str] | None,
    scaled_display: tuple[Decimal, int] | None,
    direction: str | None,
    claimed_label: str | None = None,
    change_claim: bool = False,
) -> tuple[str, str] | None:
    signed = literal.lstrip().startswith(("-", "+"))
    for item in derived:
        if item.percent != is_percent:
            continue
        allowed = item.labels | (item.pair_labels if change_claim else frozenset())
        if claimed_label is not None and claimed_label not in allowed:
            continue
        candidates = [item.value]
        if item.directional and not signed:
            if direction == "down" and item.value < 0:
                candidates = [-item.value]
            elif direction == "up" and item.value > 0:
                candidates = [item.value]
            elif direction is None:
                candidates = [abs(item.value)]
            else:
                continue
        for candidate in candidates:
            if scale is not None:
                if scaled_display is not None and _scaled_matches(
                    candidate, scale[0], scaled_display
                ):
                    return item.evidence_id, item.column
            elif _display_matches(candidate, values, literal):
                return item.evidence_id, item.column
    return None


_INDONESIAN_WORDS = frozenset({
    "apa", "berapa", "bagaimana", "kenapa", "mengapa", "yang", "dan", "untuk", "dari", "di",
    "ke", "dengan", "bulan", "tahun", "minggu", "hari", "lalu", "ini", "itu", "per", "tolong",
    "tampilkan", "bandingkan", "perbandingan", "penjualan", "pendapatan", "omzet", "naik",
    "turun", "tertinggi", "terendah", "saja", "dong", "kah", "apakah", "sampai", "dibanding",
    "januari", "februari", "maret", "mei", "juni", "juli", "agustus", "oktober", "desember",
    "kuartal", "triwulan", "berapakah", "total", "rata",
})


def is_indonesian(text: str) -> bool:
    """Heuristic for the user's language when phrasing Nova's own sentences."""
    words = re.findall(r"[a-z]+", text.casefold())
    if not words:
        return False
    hits = sum(word in _INDONESIAN_WORDS for word in words)
    return hits >= 2 or (hits == 1 and len(words) <= 4)


def _first_number(answer: str) -> str | None:
    match = _NUMBER.search(_mask_dates(answer))
    return match.group(1).strip() if match else None


def annotate_unverified(answer: str, unsupported: tuple[str, ...], *, question: str) -> str:
    """Keep the verified prose; replace only numbers no result cell supports."""
    marker = "[angka tidak terverifikasi]" if is_indonesian(question) else "[unverified number]"
    for literal in unsupported:
        answer = re.sub(r"(?<![\w.,])" + re.escape(literal) + r"(?![\w]|[.,]\d)", marker, answer)
    note = (
        "Sebagian angka dihapus karena tidak dapat diverifikasi dari hasil query."
        if is_indonesian(question)
        else "Some numbers were removed because the query result does not support them."
    )
    return answer.rstrip() + "\n\n" + note


def verified_leaders(tables: dict[str, dict[str, Any]], *, question: str) -> list[str]:
    """Highest/lowest lines computed from result cells, in the user's language."""
    rendered = render_verified_comparison(tables, question=question)
    return [
        line for line in rendered.splitlines()
        if re.search(r": (?:highest|tertinggi) ", line)
    ]


_HIGHER = (
    r"lebih\s+(?:tinggi|besar|banyak|unggul)|higher|greater|larger|bigger|more|"
    r"melebihi|mengungguli|outperform(?:s|ed)?|exceed(?:s|ed)?"
)
_LOWER = r"lebih\s+(?:rendah|kecil|sedikit)|lower|smaller|less|fewer"
_TOP = r"tertinggi|terbesar|terbanyak|paling\s+(?:tinggi|besar|banyak)|highest|largest|biggest"
_BOTTOM = r"terendah|terkecil|tersedikit|paling\s+(?:rendah|kecil|sedikit)|lowest|smallest"


@dataclass(frozen=True)
class ComparisonError:
    claim: str
    correction: str


def _label_values(tables: dict[str, dict[str, Any]]) -> list[dict[str, Decimal]]:
    """Per table: label -> first numeric value in that row."""
    output = []
    for table in tables.values():
        values: dict[str, Decimal] = {}
        for row in (table.get("rows") or [])[:200]:
            if not isinstance(row, (list, tuple)):
                continue
            label = next(
                (str(cell).strip() for cell in row
                 if isinstance(cell, str) and _number(cell) is None and cell.strip()),
                None,
            )
            number = next((_number(cell) for cell in row if _number(cell) is not None), None)
            if label and number is not None and label.casefold() not in values:
                values[label.casefold()] = number
        if len(values) >= 2:
            output.append(values)
    return output


def check_comparisons(answer: str, tables: dict[str, dict[str, Any]]) -> list[ComparisonError]:
    """Pairwise ("A higher than B") and leader ("A is the highest") claims vs result cells."""
    errors: list[ComparisonError] = []
    for values in _label_values(tables):
        labels = sorted(values, key=len, reverse=True)
        alternatives = "|".join(re.escape(label) for label in labels)
        pair = re.compile(
            rf"(?<!\w)(?P<a>{alternatives})(?!\w)[^.;\n]{{0,40}}?"
            rf"(?P<cmp>{_HIGHER}|{_LOWER})\s+(?:(?:dari|daripada|than|dibanding(?:kan)?)\s+)?"
            rf"(?P<b>{alternatives})(?!\w)",
            re.I,
        )
        for match in pair.finditer(answer):
            first, second = match["a"].casefold(), match["b"].casefold()
            if first == second:
                continue
            higher = re.fullmatch(_HIGHER, match["cmp"], re.I) is not None
            actual = values[first] > values[second] if higher else values[first] < values[second]
            if not actual and values[first] != values[second]:
                winner, loser = (
                    (first, second) if values[first] > values[second] else (second, first)
                )
                errors.append(ComparisonError(
                    match.group(0),
                    f"{_display_label(answer, winner)} ({_format(values[winner])}) > "
                    f"{_display_label(answer, loser)} ({_format(values[loser])})",
                ))
        minus = re.compile(
            rf"(?<!\w)(?P<a>{alternatives})(?!\w)\s*(?:dikurangi|minus|−|-|–)\s*"
            rf"(?P<b>{alternatives})(?!\w)[^.;\n\d-]{{0,40}}?(?P<value>-?\d[\d.,]*)",
            re.I,
        )
        for match in minus.finditer(answer):
            first, second = match["a"].casefold(), match["b"].casefold()
            stated = _number(match["value"])
            if first == second or stated is None:
                continue
            actual = values[first] - values[second]
            if stated != actual and abs(stated) == abs(actual):
                errors.append(ComparisonError(
                    match.group(0),
                    f"{_display_label(answer, first)} − {_display_label(answer, second)} = "
                    f"{_format(actual)}",
                ))
        leader = re.compile(
            rf"(?<!\w)(?P<a>{alternatives})(?!\w)\s+(?:(?:adalah|is|was|jadi|menjadi)\s+)?"
            rf"(?:(?:the|yang)\s+)?(?P<rank>{_TOP}|{_BOTTOM})",
            re.I,
        )
        for match in leader.finditer(answer):
            label = match["a"].casefold()
            top = re.fullmatch(_TOP, match["rank"], re.I) is not None
            best = max(values, key=values.get) if top else min(values, key=values.get)
            if values[label] != values[best]:
                errors.append(ComparisonError(
                    match.group(0),
                    f"{_display_label(answer, best)} ({_format(values[best])}) is the "
                    + ("highest" if top else "lowest"),
                ))
    return errors


def _display_label(answer: str, label: str) -> str:
    match = re.search(rf"(?<!\w){re.escape(label)}(?!\w)", answer, re.I)
    return match.group(0) if match else label


def _format(value: Decimal) -> str:
    return f"{value.normalize():,f}" if value == value.to_integral_value() else f"{value:,}"


@dataclass(frozen=True)
class VerifiedAnswer:
    text: str
    check: AnswerCheck
    #: True when the drafted prose was replaced by a rendered comparison.
    replaced: bool
    comparison: bool


#: A draft keeps its prose when at most this many numbers are unsupported and
#: at least one number was verified; the unsupported ones are removed in place.
_MAX_ANNOTATED = 2


def finalize_verified_answer(
    answer: str,
    *,
    question: str,
    tables: dict[str, dict[str, Any]],
    tables_shown: bool = False,
) -> VerifiedAnswer:
    """The answer shown to the user: verified prose, annotated prose, or a rendering.

    * A comparison answered with no number: the comparison rendered from cells.
    * Every number verified: the draft is kept. A comparison question also gets
      the highest/lowest lines computed from cells, so a misstated leader is
      visible next to the prose instead of replacing it.
    * A few numbers unsupported: only those numbers are removed.
    * Otherwise: a comparison rendered from result cells, in the user's language.
      ``tables_shown`` means the result tables are already on screen, so the
      rendering is sentences only instead of repeating the table.
    """
    check = check_numeric_answer(answer, question=question, tables=tables)
    comparison = is_numeric_comparison_question(question, tables)
    mistakes = check_comparisons(answer, tables)
    if mistakes:
        # A wrong "higher than" or "highest" is replaced in place and corrected
        # from result cells, even when every number in the draft is right.
        indonesian = is_indonesian(question)
        for mistake in mistakes:
            answer = answer.replace(
                mistake.claim,
                "[perbandingan dikoreksi di bawah]" if indonesian
                else "[comparison corrected below]",
            )
        heading = "Koreksi dari hasil query:" if indonesian else "Correction from the query result:"
        lines = [
            mistake.correction.translate(str.maketrans(",.", ".,")) if indonesian
            else mistake.correction
            for mistake in mistakes
        ]
        answer = answer.rstrip() + "\n\n" + heading + "\n" + "\n".join(
            f"- {line}" for line in lines
        )
        check = check_numeric_answer(answer, question=question, tables=tables)
    if check.accepted and comparison and not check.claims:
        # A comparison with no verified number is only unverifiable claims
        # ("X leads every metric"), so the comparison is shown from cells.
        rendered = render_verified_comparison(
            tables, question=question, include_table=not tables_shown
        )
        if check_numeric_answer(rendered, question=question, tables=tables).accepted:
            return VerifiedAnswer(rendered, check, True, comparison)
    if check.accepted:
        leaders = verified_leaders(tables, question=question) if comparison else []
        text = answer.rstrip() + ("\n\n" + "\n".join(leaders) if leaders else "")
        return VerifiedAnswer(text, check, False, comparison)
    data_claims = [claim for claim in check.claims if claim.evidence_id]
    headline = _first_number(answer)
    if (
        data_claims
        and len(check.unsupported) <= max(_MAX_ANNOTATED, len(data_claims))
        and headline not in check.unsupported
    ):
        # Mostly verified prose stays; only the unsupported numbers are removed.
        # A wrong headline number is not patched around: the answer is rebuilt.
        text = annotate_unverified(answer, check.unsupported, question=question)
        return VerifiedAnswer(text, check, False, comparison)
    replacement = render_verified_comparison(
        tables, question=question, include_table=not tables_shown
    )
    if check_numeric_answer(replacement, question=question, tables=tables).accepted:
        return VerifiedAnswer(replacement, check, True, comparison)
    text = (
        "Saya tidak dapat memverifikasi semua angka pada jawaban terhadap hasil query. "
        "Lihat tabel hasil di bawah."
        if is_indonesian(question)
        else "I could not verify every number in the drafted answer against the "
        "authorized query result. Review the result table below."
    )
    return VerifiedAnswer(text, check, True, comparison)


def render_verified_comparison(
    tables: dict[str, dict[str, Any]], *, question: str, include_table: bool = True
) -> str:
    """Replace a rejected draft with a comparison derived only from query cells.

    This never copies numeric claims from the rejected draft. The caller must
    check the rendered answer again before showing it.
    """
    indonesian = is_indonesian(question)

    def number(value: Decimal, places: int) -> str:
        quantum = Decimal(1).scaleb(-places)
        rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
        displayed = f"{rounded:,.{places}f}"
        return displayed.translate(str.maketrans(",.", ".,")) if indonesian else displayed

    def cell(value: Any, column: str) -> str:
        raw = str(value if value is not None else "")
        parsed = _number(value)
        if parsed is not None:
            lowered = column.casefold()
            if _PERCENT_COLUMN.search(column):
                points = parsed if abs(parsed) > 1 else parsed * 100
                raw = number(points, 2) + "%"
            elif (
                re.search(r"(?:count|quantity|units)", lowered)
                and parsed == parsed.to_integral_value()
            ):
                raw = number(parsed, 0)
            elif re.search(r"(?:revenue|amount|profit|cost|price|spend|value|total)", lowered):
                decimals = max(0, -parsed.as_tuple().exponent)
                if decimals <= 2:
                    raw = number(parsed, 0 if parsed == parsed.to_integral_value() else 2)
        return raw.replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    period_names = (
        {"current_period": "periode ini", "previous_period": "periode sebelumnya"}
        if indonesian else
        {"current_period": "current period", "previous_period": "previous period"}
    )

    def label_of(row: list[Any], positions: list[int]) -> str:
        return " / ".join(
            period_names.get(str(row[index]), str(row[index])) for index in positions
        )

    def sentences(columns: list[str], rows: list[list[Any]]) -> list[str]:
        """What the result says, with its numbers, in the user's language."""
        if not rows:
            return []
        labels = [
            index for index in range(len(columns))
            if all(_number(row[index]) is None for row in rows)
        ]
        measures = [
            index for index in range(len(columns))
            if index not in labels and columns[index].casefold() != "rank"
            and all(_number(row[index]) is not None for row in rows)
        ][:3]
        if not measures:
            return []
        name = {index: columns[index].replace("_", " ") for index in measures}
        if not labels:
            if len(rows) != 1:
                return []
            return ["; ".join(f"{name[i]}: {cell(rows[0][i], columns[i])}" for i in measures) + "."]
        if len(rows) == 1:
            return [label_of(rows[0], labels) + ": " + ", ".join(
                f"{name[i]} {cell(rows[0][i], columns[i])}" for i in measures
            ) + "."]
        periods = {str(row[labels[0]]): row for row in rows}
        if len(rows) == 2 and set(periods) == {"current_period", "previous_period"}:
            current, previous = periods["current_period"], periods["previous_period"]
            out = []
            for i in measures:
                after, before = _number(current[i]), _number(previous[i])
                line = (
                    f"{name[i]}: {period_names['current_period']} {cell(current[i], columns[i])}, "
                    f"{period_names['previous_period']} {cell(previous[i], columns[i])}"
                )
                if after is not None and before is not None:
                    delta = after - before
                    if delta == 0:
                        line += " (tetap)" if indonesian else " (unchanged)"
                    else:
                        places = 0 if delta == delta.to_integral_value() else 2
                        up = ("naik " if indonesian else "up ") if delta > 0 else (
                            "turun " if indonesian else "down "
                        )
                        change = up + number(abs(delta), places)
                        if before != 0:
                            change += "; " + number(abs(delta) / abs(before) * 100, 2) + "%"
                        line += " (" + change + ")"
                out.append(line + ".")
            return out
        out = []
        for i in measures:
            ordered = sorted(rows, key=lambda row, i=i: _number(row[i]), reverse=True)
            if len(rows) <= 6:
                out.append(f"{name[i]}: " + ", ".join(
                    f"{label_of(row, labels)} {cell(row[i], columns[i])}" for row in ordered
                ) + ".")
                continue
            high, low = ordered[0], ordered[-1]
            out.append(
                f"{name[i]}: "
                + ("tertinggi " if indonesian else "highest ")
                + f"{label_of(high, labels)} ({cell(high[i], columns[i])}); "
                + ("terendah " if indonesian else "lowest ")
                + f"{label_of(low, labels)} ({cell(low[i], columns[i])})."
            )
        return out

    sections: list[str] = []
    for table in tables.values():
        columns = [str(value)[:80] for value in (table.get("columns") or [])[:12]]
        if not columns:
            continue
        rows = [
            list(row[:len(columns)])
            for row in (table.get("rows") or [])[:20]
            if isinstance(row, (list, tuple)) and len(row) >= len(columns)
        ]
        comparisons = sentences(columns, rows)

        lines = [
            "| " + " | ".join(column.replace("|", "\\|") for column in columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
        ]
        lines.extend(
            "| " + " | ".join(
                cell(value, column) for value, column in zip(row, columns, strict=True)
            ) + " |"
            for row in rows
        )
        if (
            table.get("truncated")
            or len(table.get("rows") or []) > len(rows)
            or len(table.get("columns") or []) > len(columns)
        ):
            lines.extend(("", "Some result rows or columns were omitted from this preview."))
        elif not rows:
            lines.extend(("", "The authorized query returned no rows."))
        if include_table:
            sections.append("\n".join(comparisons + ["\n".join(lines)]))
        elif comparisons:
            sections.append("\n".join(comparisons))
    if not sections and include_table:
        return (
            "Tidak ada hasil query terotorisasi yang dapat ditampilkan."
            if indonesian else "No authorized query result is available."
        )
    if not include_table:
        # The result table is already shown; say what it contains in a sentence.
        lead = (
            "Angka berikut diambil langsung dari hasil query."
            if indonesian else "These figures come straight from the query result."
        )
        return lead + ("\n\n" + "\n".join(sections) if sections else "")
    heading = (
        "Perbandingan berdasarkan hasil query terotorisasi:"
        if indonesian else "Comparison from the authorized query result:"
    )
    return heading + "\n\n" + "\n\n".join(sections)


def is_numeric_comparison_question(
    question: str, tables: dict[str, dict[str, Any]]
) -> bool:
    """Use a complete query comparison when prose could misstate a metric leader."""
    if not re.search(r"\b(?:bandingkan|perbandingan|compare|comparison)\b", question, re.I):
        return False
    for table in tables.values():
        rows = table.get("rows") or []
        if len(rows) < 2:
            continue
        columns = table.get("columns") or []
        numeric = [
            index for index in range(len(columns))
            if all(
                isinstance(row, (list, tuple))
                and len(row) > index
                and _number(row[index]) is not None
                for row in rows
            )
        ]
        labeled = any(
            all(
                isinstance(row, (list, tuple))
                and len(row) > index
                and _number(row[index]) is None
                for row in rows
            )
            for index in range(len(columns))
        )
        if numeric and labeled:
            return True
    return False
