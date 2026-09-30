from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceSpan:
    start: int
    end: int
    line: int
    column: int

    @classmethod
    def from_context(cls, node: Any) -> SourceSpan:
        return cls(node.start.start, node.stop.stop + 1, node.start.line, node.start.column)


@dataclass(frozen=True, slots=True)
class SqlFragment:
    span: SourceSpan
    source: str = field(repr=False)

    @property
    def sql(self) -> str:
        return self.source[self.span.start : self.span.end]
