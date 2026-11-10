"""Turn kernel values into JSON the notebook UI renders predictably."""

from __future__ import annotations

import datetime as _dt
import decimal
import math
import uuid
from typing import Any

MAX_TEXT = 2000


def cell_value(value: Any) -> Any:
    """One table cell as a JSON value.

    Numbers stay numbers when they are finite; NaN and infinities become the
    strings a reader expects. Temporal values use ISO text, decimals too long
    for a double keep their exact digits as text, binary is described rather
    than dumped, and nested values are rendered as compact text.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        # JavaScript loses precision past 2**53; keep exact digits.
        return value if -(2**53) <= value <= 2**53 else str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, decimal.Decimal):
        # Exact digits survive as a number when a double holds them (up to 15
        # significant digits); longer values stay text rather than round.
        if not value.is_finite():
            return str(value)
        digits = len(value.as_tuple().digits)
        if digits <= 15:
            return int(value) if value == value.to_integral_value() else float(value)
        return str(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…"
    item = getattr(value, "item", None)
    if callable(item):  # numpy scalars
        try:
            return cell_value(item())
        except Exception:  # noqa: BLE001 - fall back to text
            pass
    text = repr(value)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"
