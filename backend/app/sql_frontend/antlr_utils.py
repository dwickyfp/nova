from collections.abc import Iterator
from typing import Any

from antlr4 import InputStream


class CaseInsensitiveInputStream(InputStream):
    def LA(self, offset: int) -> int:  # noqa: N802
        char = super().LA(offset)
        return char - 32 if 97 <= char <= 122 else char


def walk_nodes(tree: Any) -> Iterator[Any]:
    pending = [tree]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(getattr(node, "children", None) or []))
