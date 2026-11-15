"""Dataset execution provenance without its private source definition."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class DatasetQuery:
    dataset_id: UUID
    operation: Literal["inspect", "preview"]
    revision: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_id, UUID) or self.operation not in {"inspect", "preview"}:
            raise ValueError("Invalid dataset query provenance")
        if self.revision is not None and (type(self.revision) is not int or self.revision < 1):
            raise ValueError("Invalid dataset query revision")

    @property
    def label(self) -> str:
        version = f" revision {self.revision}" if self.revision is not None else ""
        return f"Dataset {self.dataset_id}{version}: {self.operation}"


_DATASET_QUERY: ContextVar[DatasetQuery | None] = ContextVar("dataset_query", default=None)


def current_dataset_query() -> DatasetQuery | None:
    return _DATASET_QUERY.get()


@contextmanager
def dataset_query(provenance: DatasetQuery) -> Iterator[None]:
    token = _DATASET_QUERY.set(provenance)
    try:
        yield
    finally:
        _DATASET_QUERY.reset(token)
