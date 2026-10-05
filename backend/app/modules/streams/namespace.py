"""Canonical names for internal Stream relations; no session or credential state."""

from dataclasses import dataclass

from app.sql_frontend.errors import SemanticError


@dataclass(frozen=True, slots=True)
class StreamName:
    database: str
    name: str
    schema: str = "default"
    catalog: str = "default_catalog"

    def __post_init__(self) -> None:
        if self.schema != "default" or self.catalog != "default_catalog":
            raise SemanticError("Streams require the internal catalog and default schema")
        if any(
            not value or len(value) > 128 or "\x00" in value
            for value in (self.database, self.name)
        ):
            raise SemanticError("A database and object name are required for Streams")

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.catalog, self.database, self.schema, self.name

    @property
    def qualified(self) -> str:
        return ".".join("`" + part.replace("`", "``") + "`" for part in (
            self.database, self.schema, self.name,
        ))


def resolve_stream_name(parts: tuple[str, ...], database: str | None) -> StreamName:
    if len(parts) == 1:
        if not database:
            raise SemanticError("An active database is required for an unqualified Stream name")
        return StreamName(database, parts[0])
    if len(parts) == 2:
        return StreamName(parts[0], parts[1])
    if len(parts) == 3 and parts[1].casefold() == "default":
        return StreamName(parts[0], parts[2])
    raise SemanticError("Expected stream, database.stream, or database.default.stream")
