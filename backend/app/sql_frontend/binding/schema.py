from dataclasses import dataclass

from app.sql_frontend.binding.models import (
    BoundColumn,
    BoundOutputColumn,
    BoundRelation,
    BoundTable,
)
from app.sql_frontend.binding.types import TypeCompatibility, TypeCompatibilityChecker
from app.sql_frontend.errors import BindingError


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    source: BoundOutputColumn
    target: BoundOutputColumn
    compatibility: TypeCompatibility


def _unique(relation: BoundRelation) -> dict[str, BoundOutputColumn]:
    names = {}
    for column in relation.columns:
        key = column.name.casefold()
        if key in names:
            raise BindingError("Duplicate output name prevents name-based mapping")
        names[key] = column
    return names


def map_by_name(source: BoundRelation, target: BoundRelation) -> tuple[ColumnMapping, ...]:
    inputs, outputs = _unique(source), _unique(target)
    if outputs.keys() - inputs.keys():
        raise BindingError("Missing source columns for name-based mapping")
    if inputs.keys() - outputs.keys():
        raise BindingError("Extra source columns for name-based mapping")
    result = []
    for column in target.columns:
        input_column = inputs[column.name.casefold()]
        compatibility = TypeCompatibilityChecker.query_assignment(
            input_column.sql_type, column.sql_type
        )
        if compatibility not in {
            TypeCompatibility.EXACT,
            TypeCompatibility.SAFE_WIDEN,
            TypeCompatibility.IMPLICIT_CAST,
        }:
            raise BindingError("Column types are not safely compatible")
        result.append(ColumnMapping(input_column, column, compatibility))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class AddColumnDelta:
    column: BoundOutputColumn


@dataclass(frozen=True, slots=True)
class SchemaDelta:
    additions: tuple[AddColumnDelta, ...] = ()
    unsafe_reasons: tuple[str, ...] = ()

    @property
    def safe_candidate(self) -> bool:
        return not self.unsafe_reasons


def alter_eligibility(column: BoundColumn, capabilities=None) -> tuple[str, ...]:
    reasons = []
    if column.generated_expression:
        reasons.append("generated_column")
    for attribute in ("auto_increment", "key_column", "hidden", "partition_column"):
        value = getattr(column, attribute)
        if value is not False:
            reasons.append(attribute if value else attribute + "_unknown")
    if capabilities is not None and not capabilities.merge_schema_evolution:
        reasons.append("schema_evolution_capability_disabled")
    return tuple(reasons)


def compare_schema(source: BoundRelation, target: BoundTable) -> SchemaDelta:
    inputs = _unique(source)
    columns = {column.name.casefold(): column for column in target.columns}
    reasons = []
    for name, column in columns.items():
        incoming = inputs.get(name)
        if incoming is None:
            reasons.append("missing_target_column")
            continue
        compatibility = TypeCompatibilityChecker.compare(column.sql_type, incoming.sql_type)
        if compatibility != TypeCompatibility.EXACT:
            reasons.extend(alter_eligibility(column))
            if compatibility not in {TypeCompatibility.EXACT, TypeCompatibility.SAFE_WIDEN}:
                reasons.append("unsafe_type_difference")
            else:
                reasons.append("alter_support_requires_engine_validation")
        if column.generated_expression:
            reasons.append("generated_column_conflict")
        if incoming.nullable is True and column.nullable is False:
            reasons.append("nullable_constraint_conflict")
    additions = tuple(
        AddColumnDelta(column) for name, column in inputs.items() if name not in columns
    )
    if any(delta.column.sql_type.kind == "UNKNOWN" for delta in additions):
        reasons.append("unknown_addition_type")
    if additions and (target.key_type is None or target.partition_columns is None):
        reasons.append("incomplete_table_constraints")
    return SchemaDelta(additions, tuple(dict.fromkeys(reasons)))
