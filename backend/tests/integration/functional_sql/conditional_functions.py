from .contracts import Case, FunctionSignature
from .functions import argument_types, typed


def conditional_case(function: FunctionSignature) -> Case | None:
    name = function.name
    if name not in {"coalesce", "if", "ifnull", "nullif", "greatest", "least"}:
        return None
    types = argument_types(function.signature)
    if types and types[-1] == "...":
        types.pop()
    output_type = types[1] if name == "if" else types[0]
    if output_type in {"UNKNOWN_TYPE", "INVALID_TYPE", "NULL_TYPE"}:
        return Case(
            "function." + function.key,
            "conditional",
            "",
            signatures=(function.key,),
            prerequisite=f"Catalog type {output_type} needs a verified user expression",
        )
    if output_type in {"DATE", "DATETIME"}:
        first, second = "2024-01-01", "2024-01-02"
        expected = second if name == "greatest" else first
        if output_type == "DATETIME":
            expected += " 00:00:00"
        oracle = "equals"
    elif output_type == "VARCHAR":
        first, second = "a", "b"
        expected, oracle = (second if name == "greatest" else first), "equals"
    elif output_type == "JSON":
        first, second = {"a": 1}, {"a": 2}
        expected, oracle = (second if name == "greatest" else first), "json"
    elif output_type in {"ANY_ARRAY", "ARRAY"}:
        first, second = [1, 2], [3, 4]
        expected, oracle = first, "json"
    elif output_type in {"ANY_MAP", "MAP", "ANY_STRUCT", "STRUCT"}:
        first, second = {"k": 1}, {"k": 2}
        expected, oracle = first, "json"
    else:
        first, second = 1, 2
        expected, oracle = (second if name == "greatest" else first), "number"
    left, right = typed(output_type, first), typed(output_type, second)
    if output_type in {"ANY_MAP", "MAP"}:
        left, right = "map{'k':CAST(1 AS BIGINT)}", "map{'k':CAST(2 AS BIGINT)}"
    elif output_type in {"ANY_STRUCT", "STRUCT"}:
        left, right = "named_struct('k',CAST(1 AS BIGINT))", "named_struct('k',CAST(2 AS BIGINT))"
    if output_type == "BOOLEAN":
        right = "CAST(FALSE AS BOOLEAN)"
        expected = 0 if name == "least" else 1
    expression = f"{name}({('TRUE,' if name == 'if' else '')}{left},{right})"
    if output_type == "VARBINARY":
        expression = f"hex({expression})"
        expected, oracle = [["32" if name == "greatest" else "31"]], "equals"
    elif output_type == "BITMAP":
        expression = f"bitmap_count({expression})"
        expected, oracle = 1, "number"
    elif output_type == "HLL":
        expression = f"hll_cardinality({expression})"
        expected, oracle = 1, "number"
    elif output_type == "PERCENTILE":
        expression = f"percentile_approx_raw({expression},0.5)"
        expected, oracle = 1, "number"
    if oracle == "equals" and not isinstance(expected, list):
        expected = [[expected]]
    return Case(
        "function." + function.key,
        "conditional",
        "SELECT " + expression,
        expected,
        oracle,
        signatures=(function.key,),
    )
