from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.sql_frontend.parser import ParsedStatement


@dataclass(frozen=True, slots=True)
class Statement:
    parsed: ParsedStatement = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class NativeStatement(Statement):
    pass


@dataclass(frozen=True, slots=True)
class StreamStatement(Statement):
    operation: str
    name: tuple[str, ...] | None = None
    source: tuple[str, ...] | None = None
    if_exists: bool = False
    if_not_exists: bool = False


@dataclass(frozen=True, slots=True)
class StageAwareStatement(Statement):
    pass


@dataclass(frozen=True, slots=True)
class CreateTaskStatement(Statement):
    pass


@dataclass(frozen=True, slots=True)
class CreateMLModelStatement(Statement):
    pass


@dataclass(frozen=True, slots=True)
class MLPredictStatement(Statement):
    call: Any = field(repr=False)


@dataclass(frozen=True, slots=True)
class MLMaterializeStatement(Statement):
    pass


@dataclass(frozen=True, slots=True)
class MLForecastStatement(Statement):
    pass


@dataclass(frozen=True, slots=True)
class SecurityStatement(Statement):
    pass


@dataclass(frozen=True, slots=True)
class ForcePasswordChangeStatement(Statement):
    pass
