from .contracts import Case, FunctionSignature
from .functions import argument_types, typed


def metric_case(function: FunctionSignature) -> Case | None:
    name = function.name
    types = argument_types(function.signature)
    left, right = "bitmap_from_string('1,2,3')", "bitmap_from_string('2,3,4')"
    numbers = {
        "bitmap_count": (left, 3),
        "bitmap_contains": (left + ",2", 1),
        "bitmap_has_any": (left + "," + right, 1),
        "bitmap_min": (left, 1),
        "bitmap_max": (left, 3),
        "hll_cardinality": ("hll_hash('one')", 1),
        "percentile_approx_raw": ("percentile_hash(3),0.5", 3),
    }
    strings = {
        "bitmap_to_string": (left, "1,2,3"),
        "bitmap_to_base64": ("bitmap_empty()", "AA=="),
    }
    bitmap_results = {
        "bitmap_and": (left + "," + right, "2,3"),
        "bitmap_andnot": (left + "," + right, "1"),
        "bitmap_or": (left + "," + right, "1,2,3,4"),
        "bitmap_xor": (left + "," + right, "1,4"),
        "bitmap_remove": (left + ",2", "1,3"),
        "bitmap_subset_in_range": (left + ",2,4", "2,3"),
        "bitmap_subset_limit": (left + ",1,2", "1,2"),
        "sub_bitmap": (left + ",1,2", "2,3"),
        "bitmap_from_string": ("'1,2,3'", "1,2,3"),
        "array_to_bitmap": ("[1,2,3]", "1,2,3"),
        "bitmap_empty": ("", ""),
        "base64_to_bitmap": ("bitmap_to_base64(" + left + ")", "1,2,3"),
        "bitmap_from_binary": ("bitmap_to_binary(" + left + ")", "1,2,3"),
    }
    oracle = "equals"
    if name in numbers:
        arguments, expected = numbers[name]
        if name == "hll_cardinality" and types == ["VARCHAR"]:
            arguments = "hll_serialize(hll_hash('one'))"
        expression, oracle = f"{name}({arguments})", "number"
    elif name in strings:
        arguments, expected = strings[name]
        expression, expected = f"{name}({arguments})", [[expected]]
    elif name in bitmap_results:
        arguments, expected = bitmap_results[name]
        if name == "array_to_bitmap":
            arguments = "[CAST(1 AS BIGINT),CAST(2 AS BIGINT),CAST(3 AS BIGINT)]"
        expression, expected = f"bitmap_to_string({name}({arguments}))", [[expected]]
    elif name == "to_bitmap":
        expression = f"bitmap_to_string(to_bitmap({typed(types[0], 7)}))"
        expected = [["1" if types[0] == "BOOLEAN" else "7"]]
    elif name == "bitmap_to_array":
        expression, expected, oracle = f"bitmap_to_array({left})", [1, 2, 3], "json"
    elif name == "hll_empty":
        expression, expected, oracle = "hll_cardinality(hll_empty())", 0, "number"
    elif name == "hll_hash":
        expression, expected, oracle = "hll_cardinality(hll_hash('one'))", 1, "number"
    elif name in {"hll_deserialize", "hll_serialize"}:
        expression = "hll_cardinality(hll_deserialize(hll_serialize(hll_hash('one'))))"
        expected, oracle = 1, "number"
    elif name == "bitmap_to_binary":
        expression, expected = (
            f"bitmap_to_string(bitmap_from_binary(bitmap_to_binary({left})))",
            [["1,2,3"]],
        )
    elif name in {"percentile_hash", "percentile_empty"}:
        expression = f"percentile_approx_raw({name}({'3' if name.endswith('hash') else ''}),0.5)"
        expected = [[3.0]] if name.endswith("hash") else None
        if name == "percentile_empty":
            oracle = "nan"
    else:
        return None
    return Case(
        "function." + function.key,
        "complex",
        "SELECT " + expression,
        expected,
        oracle,
        signatures=(function.key,),
    )
