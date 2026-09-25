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
    r"\b(?:fell|dropped|declined|decreased|turun|menurun|berkurang)\s+"
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
    cells: list[tuple[Decimal, str, str, bool, bool, tuple[str, ...]]] = []
    column_names: set[str] = set()
    row_labels: set[str] = set()
    for evidence_id, table in tables.items():
        columns = [str(col) for col in table.get("columns") or []]
        column_names.update(columns)
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
    claims: list[NumericClaim] = []
    unsupported: list[str] = []
    for match in _NUMBER.finditer(answer):
        literal = match.group(1).strip()
        is_percent = literal.endswith("%")
        values = _percent_values(literal) if is_percent else _candidates(literal.rstrip("% "))
        if not values:
            unsupported.append(literal)
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


def render_verified_comparison(
    tables: dict[str, dict[str, Any]], *, question: str
) -> str:
    """Replace a rejected draft with a comparison derived only from query cells.

    This never copies numeric claims from the rejected draft. The caller must
    check the rendered answer again before showing it.
    """
    indonesian = bool(re.search(r"\b(?:bandingkan|berapa|untuk|perbandingan)\b", question, re.I))

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
            elif re.search(r"(?:revenue|amount|profit|cost|price)", lowered):
                decimals = max(0, -parsed.as_tuple().exponent)
                if decimals <= 2:
                    raw = number(parsed, 2)
        return raw.replace("|", "\\|").replace("\n", " ").replace("\r", " ")

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
        label_position = next(
            (
                index for index in range(len(columns))
                if rows and all(_number(row[index]) is None for row in rows)
            ),
            None,
        )
        comparisons: list[str] = []
        if label_position is not None and len(rows) > 1:
            for index, column in enumerate(columns):
                if index == label_position:
                    continue
                values = [_number(row[index]) for row in rows]
                if any(value is None for value in values):
                    continue
                highest = max(range(len(rows)), key=lambda position: values[position])
                lowest = min(range(len(rows)), key=lambda position: values[position])
                name = column.replace("_", " ")
                high_label = str(rows[highest][label_position])
                low_label = str(rows[lowest][label_position])
                if indonesian:
                    comparisons.append(
                        f"{name}: tertinggi {high_label}; terendah {low_label}."
                    )
                else:
                    comparisons.append(
                        f"{name}: highest {high_label}; lowest {low_label}."
                    )

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
        sections.append("\n".join(comparisons + ["\n".join(lines)]))
    if not sections:
        return (
            "Tidak ada hasil query terotorisasi yang dapat ditampilkan."
            if indonesian else "No authorized query result is available."
        )
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
