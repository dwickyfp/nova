from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class PlanEffects:
    reads_data: bool = False
    writes_data: bool = False
    deletes_rows: bool = False
    changes_schema: bool = False
    changes_security: bool = False
    external_io: bool = False
    writes_metadata: bool = False
    updates_rows: bool = False
    replaces_data: bool = False
    drops_objects: bool = False

    @property
    def mutates(self) -> bool:
        return any(
            getattr(self, name)
            for name in (
                "writes_data",
                "deletes_rows",
                "changes_schema",
                "changes_security",
                "writes_metadata",
                "updates_rows",
                "replaces_data",
                "drops_objects",
            )
        )

    def __or__(self, other: "PlanEffects") -> "PlanEffects":
        return PlanEffects(
            **{f.name: getattr(self, f.name) or getattr(other, f.name) for f in fields(self)}
        )

    def includes(self, other: "PlanEffects") -> bool:
        return all(not getattr(other, f.name) or getattr(self, f.name) for f in fields(self))
