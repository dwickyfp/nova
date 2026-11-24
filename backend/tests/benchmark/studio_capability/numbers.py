"""Read the numbers an answer states, in English and Indonesian notation.

A Studio answer writes the same value many ways: ``Rp 175.184.000``,
``175,184,000``, ``175,2 juta``, ``175.18M``, ``1,6 miliar``. Every number found
is returned as one or more *candidates*, each with the resolution its display
precision implies, so ``175.2M`` matches 175,184,000 but not 176,000,000.

Separators are ambiguous on their own (``175.184`` is a decimal in English and
175 thousand in Indonesian), so an ambiguous token yields both readings. The
match is then a question of whether *any* reading is within tolerance, which
is the generous direction: a wrong value still fails because neither reading
is near the expected one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Word or letter suffix -> multipliers to try. ``M`` is a million in English
#: and a billion (miliar) in Indonesian business shorthand.
_SUFFIX: dict[str, tuple[float, ...]] = {
    "k": (1e3,), "rb": (1e3,), "ribu": (1e3,), "thousand": (1e3,),
    "m": (1e6, 1e9), "mn": (1e6,), "mio": (1e6,), "jt": (1e6,), "juta": (1e6,),
    "million": (1e6,), "millions": (1e6,),
    "b": (1e9,), "bn": (1e9,), "billion": (1e9,), "miliar": (1e9,), "milyar": (1e9,),
    "t": (1e12,), "tn": (1e12,), "triliun": (1e12,), "trillion": (1e12,),
}

_TOKEN = re.compile(
    r"(?<![\w.,])(?P<sign>[-−])?(?:rp\.?\s?|idr\s?|usd\s?|\$\s?)?"
    r"(?P<num>\d+(?:[.,]\d+)*)"
    r"(?:\s?(?P<suffix>ribu|rb|thousand|k|juta|jt|millions?|mio|mn|m|miliar|milyar|billion|bn|b"
    r"|triliun|trillion|tn|t)\b\.?)?"
    r"(?P<pct>\s?%|\s?persen|\s?percent)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Candidate:
    value: float
    #: Half the place value of the last displayed digit, after the multiplier.
    resolution: float
    percent: bool = False


def _readings(raw: str) -> list[tuple[float, float]]:
    """``(value, resolution)`` for every plausible reading of a separator string."""
    dots, commas = raw.count("."), raw.count(",")
    out: list[tuple[float, float]] = []

    def decimal(text: str, sep: str, thousands: str) -> None:
        body = text.replace(thousands, "") if thousands else text
        if sep and sep in body:
            whole, _, frac = body.rpartition(sep)
            if not whole.isdigit() or not frac.isdigit():
                return
            out.append((float(f"{whole}.{frac}"), 0.5 * 10 ** -len(frac)))
        elif body.isdigit():
            out.append((float(body), 0.5))

    if dots and commas:
        if raw.rfind(".") > raw.rfind(","):
            decimal(raw, ".", ",")
        else:
            decimal(raw, ",", ".")
        return out
    sep = "." if dots else "," if commas else ""
    if not sep:
        decimal(raw, "", "")
        return out
    groups = raw.split(sep)
    grouped = all(len(g) == 3 for g in groups[1:]) and 1 <= len(groups[0]) <= 3
    if grouped:
        decimal(raw, "", sep)  # thousands separators
    if len(groups) == 2:
        decimal(raw, sep, "")  # a decimal point or comma
    return out


def numbers_in(text: str) -> list[Candidate]:
    """Every numeric reading in ``text``."""
    found: list[Candidate] = []
    for match in _TOKEN.finditer(text or ""):
        raw = match.group("num")
        suffix = (match.group("suffix") or "").lower().rstrip(".")
        percent = bool(match.group("pct"))
        negative = bool(match.group("sign"))
        multipliers = _SUFFIX.get(suffix, (1.0,))
        for value, resolution in _readings(raw):
            for multiplier in multipliers:
                signed = -value if negative else value
                found.append(Candidate(signed * multiplier, resolution * multiplier, percent))
    return found


def matches(expected: float, candidates: list[Candidate], *, rel_tol: float = 0.002) -> bool:
    """Whether a stated number is ``expected`` within its display precision."""
    for candidate in candidates:
        allowed = max(candidate.resolution, abs(expected) * rel_tol, 1e-9)
        if abs(candidate.value - expected) <= allowed:
            return True
        # A decline may be written "-79.9%" or "turun 79,9%".
        if candidate.percent and expected < 0 < candidate.value and abs(
                candidate.value + expected) <= allowed:
            return True
        # A share may be written as 0.25 or 25%.
        if candidate.percent and abs(candidate.value / 100 - expected) <= max(
            candidate.resolution / 100, abs(expected) * rel_tol, 1e-9
        ):
            return True
    return False


def as_number(value: object) -> float | None:
    """A table cell as a float, when it is one."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            readings = numbers_in(text)
            return readings[0].value if len(readings) == 1 else None
    return None
