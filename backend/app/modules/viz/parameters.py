"""Resolve private dataset inputs after document access, before source execution."""

from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parameters import BoundParameters, ParameterValue

from .contracts.parameters import Parameter, ParameterInput


def bind_parameters(
    declarations: tuple[Parameter, ...], supplied: tuple[ParameterInput, ...],
) -> BoundParameters | None:
    values = {item.parameter_id: item.values for item in supplied}
    names = {item.parameter_id for item in declarations}
    if len(values) != len(supplied) or not values.keys() <= names:
        raise SemanticError("Unknown or duplicate dataset parameter input")
    if not declarations:
        return None
    if any(item.sensitive for item in declarations):
        # Engine log/process-list protection must be accepted before transmitting
        # sensitive values; Nova-side redaction alone does not protect that boundary.
        raise SemanticError("Sensitive dataset parameters require protected engine execution")
    resolved: list[tuple[str, ParameterValue]] = []
    for declaration in declarations:
        if declaration.parameter_id in values:
            selected = values[declaration.parameter_id]
        elif declaration.defaults:
            selected = declaration.defaults
        elif not declaration.required and declaration.nullable:
            selected = (None,)
        else:
            raise SemanticError("A required dataset parameter value is missing")
        try:
            resolved.append((declaration.parameter_id, declaration.checked_values(selected)))
        except ValueError:
            raise SemanticError("Dataset parameter input violates its declaration") from None
    return BoundParameters(tuple(item.specification() for item in declarations), tuple(resolved))
