"""Numbers as people write them, in any language, from CLDR locale data.

A number in an answer can be "1.234,5", "1,234.5", "1 234,5", "١٢٣", "1,2 juta",
"1.2M", "1,2 Mio.", "Rp1.234", or "1.2万". Nothing here is a hand-written word
list: digits come from Unicode; separators, compact scales (juta, Mio., 万, 億)
and currency symbols from the Unicode CLDR data that Babel ships.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache

from babel import Locale, UnknownLocaleError
from babel.numbers import format_decimal, get_decimal_symbol

#: Signs that make a number a percentage, in any script.
PERCENT_SIGNS = ("%", "٪", "％")
#: Digits, then separator-digit groups. Separators: . , apostrophe, NBSP, NNBSP,
#: Arabic decimal and group marks. A plain space never joins two numbers.
_NUMBER = re.compile(r"(?<![0-9.,])([-+−]?\d+(?:[.,'  ٫٬]\d+)*)")
_WORD_BEFORE = re.compile(r"([A-Za-z_$]{1,4})$")
#: Powers of ten a unit word can stand for (thousand .. trillion, and 万 / 億).
UNIT_POWERS = tuple(Decimal(10) ** power for power in (3, 4, 6, 8, 9, 12))


@dataclass(frozen=True)
class NumberToken:
    start: int
    end: int
    #: The text as written, including a percent sign or a compact scale.
    text: str
    #: The digits and separators alone.
    core: str
    #: Every reading of the digits (1.234 may be 1234 or 1.234).
    values: frozenset[Decimal]
    percent: bool
    #: The compact scale written after the number (1e6 for "juta"), if any.
    scale: Decimal | None
    #: Digits after the decimal mark, for comparing rounded displays.
    places: int


def ascii_digits(text: str) -> str:
    """Map every Unicode decimal digit (١, ๓, ３) to 0-9; positions are kept."""
    return "".join(
        str(unicodedata.decimal(char)) if char.isdigit() and not char.isascii() else char
        for char in text
    )


def _locale(language: str) -> Locale | None:
    try:
        return Locale.parse(str(language or "en").replace("-", "_"))
    except (UnknownLocaleError, ValueError, TypeError):
        return None


@lru_cache(maxsize=64)
def compact_scales(language: str) -> tuple[tuple[str, Decimal, str], ...]:
    """(written unit, multiplier, decimal symbol) for compact numbers, longest first.

    From CLDR compact patterns: "0 juta" at 1000000 means "juta" is 1e6; "00万"
    at 100000 means "万" is 1e4. English is always included, since answers in any
    language often use "M" or "million". The decimal symbol is the one of the
    locale the unit belongs to, so "1.234 juta" reads as Indonesian writes it.
    """
    units: dict[str, tuple[Decimal, str]] = {}
    for tag in dict.fromkeys((language, "en")):
        locale = _locale(tag)
        if locale is None:
            continue
        decimal_symbol = get_decimal_symbol(locale)
        for table in locale.compact_decimal_formats.values():
            for patterns in table.values():
                for magnitude, pattern in patterns.items():
                    text = getattr(pattern, "pattern", str(pattern)).replace("'", "")
                    zeros = text.count("0")
                    unit = " ".join(text.replace("0", "").split())
                    if not zeros or not unit:
                        continue
                    multiplier = Decimal(int(magnitude)) / Decimal(10) ** (zeros - 1)
                    units.setdefault(unit.casefold(), (multiplier, decimal_symbol))
    return tuple(
        (unit, multiplier, symbol)
        for unit, (multiplier, symbol) in sorted(units.items(), key=lambda item: -len(item[0]))
    )


#: Locales whose currency symbols are recognised before a number, whatever the
#: answer's language ("Rp" is written in English answers about Indonesia too).
_CURRENCY_LOCALES = (
    "en", "id", "ms", "ja", "zh", "ko", "ar", "hi", "th", "vi", "es", "pt", "fr", "de",
    "it", "nl", "ru", "tr", "pl", "sv", "fil", "bn", "ur", "fa", "he",
)


@lru_cache(maxsize=64)
def currency_prefixes(language: str) -> frozenset[str]:
    """Currency symbols and ISO codes that may sit right before a number (Rp, US$)."""
    prefixes: set[str] = set()
    for tag in dict.fromkeys((language, *_CURRENCY_LOCALES)):
        locale = _locale(tag)
        if locale is None:
            continue
        for code, symbol in locale.currency_symbols.items():
            prefixes.add(code)
            prefixes.add(symbol)
    return frozenset(prefix for prefix in prefixes if prefix)


def _readings(core: str) -> frozenset[Decimal]:
    """Every sensible value of "1.234", "1,234.5", "1.234,5", "1'234"."""
    raw = core.replace("−", "-").replace(" ", "").replace(" ", "")
    raw = raw.replace("'", "").replace("٬", "").replace("٫", ".")
    sign = ""
    if raw[:1] in "+-":
        sign, raw = raw[0], raw[1:]
    marks = [char for char in raw if char in ".,"]
    options: set[str] = set()
    if not marks:
        options.add(raw)
    elif len(set(marks)) == 2:
        decimal_mark = "." if raw.rfind(".") > raw.rfind(",") else ","
        group_mark = "," if decimal_mark == "." else "."
        options.add(raw.replace(group_mark, "").replace(decimal_mark, "."))
    else:
        mark = marks[0]
        groups = raw.split(mark)
        if len(marks) > 1:
            options.add(raw.replace(mark, ""))
        else:
            options.add(raw.replace(mark, "."))
            if len(groups[1]) == 3 and 1 <= len(groups[0]) <= 3:
                options.add(raw.replace(mark, ""))
    values = set()
    for option in options:
        try:
            values.add(Decimal(sign + option))
        except InvalidOperation:
            continue
    return frozenset(values)


def _places(core: str) -> int:
    digits = core.replace(" ", "").replace(" ", "").replace("'", "")
    marks = [index for index, char in enumerate(digits) if char in ".,٫"]
    if not marks:
        return 0
    last = marks[-1]
    after = len(digits) - last - 1
    if digits[last] == "٫" or len({digits[index] for index in marks}) == 2:
        return after  # the last mark is the decimal one
    # One kind of mark before groups of three is grouping ("1.234", "3.368.049").
    return 0 if after == 3 else after


def _decimal_reading(values: frozenset[Decimal]) -> frozenset[Decimal]:
    fractional = frozenset(value for value in values if value != value.to_integral_value())
    return fractional or values


def _single_mark(core: str) -> str | None:
    marks = [char for char in core if char in ".,"]
    return marks[0] if len(marks) == 1 else None


def number_tokens(text: str, language: str = "en") -> list[NumberToken]:
    """Numbers in ``text``, with percent signs and compact scales attached."""
    normalized = ascii_digits(text)
    scales = compact_scales(language)
    currencies = currency_prefixes(language)
    tokens = []
    for match in _NUMBER.finditer(normalized):
        word = _WORD_BEFORE.search(normalized[max(0, match.start() - 4):match.start()])
        if word and word.group(1) not in currencies and not any(
            word.group(1).endswith(prefix) for prefix in currencies if len(prefix) > 1
        ):
            continue  # "Q2", "H2O", "v3": part of a word, not a number
        core = match.group(1)
        end = match.end()
        rest = normalized[end:end + 24]
        values = _readings(core)
        percent = False
        scale = None
        stripped = rest.lstrip("   ")
        if stripped[:1] in PERCENT_SIGNS:
            percent = True
            end += len(rest) - len(stripped) + 1
        else:
            lowered = stripped.casefold()
            for unit, multiplier, decimal_symbol in scales:
                if not lowered.startswith(unit):
                    continue
                after = lowered[len(unit):len(unit) + 1]
                # A unit made of letters must end at a word boundary ("M" but not "Medan").
                latin = unit[-1:].isascii() and after.isascii()
                if latin and unit[-1:].isalpha() and after.isalpha():
                    continue
                scale = multiplier
                end += len(rest) - len(stripped) + len(unit)
                mark = _single_mark(core)
                if mark is not None and len(values) > 1:
                    # "1.234 juta" is read the way the unit's language writes numbers.
                    values = (
                        _decimal_reading(values) if mark == decimal_symbol
                        else frozenset(v for v in values if v == v.to_integral_value())
                    )
                break
        if not percent and match.start() > 0 and normalized[match.start() - 1] in PERCENT_SIGNS:
            percent = True
        if percent:
            values = _decimal_reading(values)  # "37,028%" is never 37028%
        tokens.append(NumberToken(
            start=match.start(), end=end, text=text[match.start():end], core=core,
            values=values, percent=percent, scale=scale, places=_places(core),
        ))
    return tokens


def with_scale(token: NumberToken, scale: Decimal, text: str) -> NumberToken:
    """The token read with a unit word the locale data does not know."""
    return replace(token, scale=scale, text=text)


def display_matches(value: Decimal, token: NumberToken) -> bool:
    """Whether ``value`` shows as ``token``, rounded as the token is written.

    Each reading carries its own precision: "1.234" is 1234 or 1.234.
    """
    shown = value / token.scale if token.scale else value
    for reading in token.values:
        quantum = Decimal(1).scaleb(min(reading.as_tuple().exponent, 0))
        try:
            if shown.quantize(quantum, rounding=ROUND_HALF_UP) == reading:
                return True
        except InvalidOperation:
            continue
    return False


def format_number(value: Decimal, language: str, places: int = 0) -> str:
    """A number formatted as the language writes it (1.234,5 / 1,234.5)."""
    locale = _locale(language) or _locale("en")
    quantum = Decimal(1).scaleb(-places)
    rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
    pattern = "#,##0" + ("." + "0" * places if places else "")
    return format_decimal(rounded, format=pattern, locale=locale)


def format_percent(value: Decimal, language: str, places: int = 2) -> str:
    """Percentage points with a percent sign (37,03%)."""
    return format_number(value, language, places) + "%"
