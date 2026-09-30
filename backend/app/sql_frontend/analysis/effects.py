from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class PlanEffects:
    reads_data: bool = False
    writes_data: bool = False
    deletes_rows: bool = False
    changes_schema: bool = False
    changes_security: bool = False
    external_io: bool = False

    def __or__(self, other: "PlanEffects") -> "PlanEffects":
        return PlanEffects(
            **{f.name: getattr(self, f.name) or getattr(other, f.name) for f in fields(self)}
        )

    def includes(self, other: "PlanEffects") -> bool:
        return all(not getattr(other, f.name) or getattr(self, f.name) for f in fields(self))
