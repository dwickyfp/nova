from __future__ import annotations

from .contracts import Case, FunctionSignature
from .functions import argument_types, typed

RECIPES = {
    "array_length": ("array_length([1,2,3])", 3, "number"),
    "array_min": ("array_min([3,1,2])", 1, "number"),
    "array_max": ("array_max([3,1,2])", 3, "number"),
    "array_sum": ("array_sum([1,2,3])", 6, "number"),
    "array_avg": ("array_avg([1,2,3])", 2, "number"),
    "array_append": ("array_append([1,2],3)", [1, 2, 3], "json"),
    "array_remove": ("array_remove([1,2,1],1)", [2], "json"),
    "array_sort": ("array_sort([3,1,2])", [1, 2, 3], "json"),
    "array_distinct": ("array_sort(array_distinct([1,2,1]))", [1, 2], "json"),
    "array_difference": ("array_difference([1,3,6])", [0, 2, 3], "json"),
    "array_cum_sum": ("array_cum_sum([1,2,3])", [1, 3, 6], "json"),
    "array_concat": ("array_concat([1,2],[3])", [1, 2, 3], "json"),
    "array_intersect": ("array_sort(array_intersect([1,2],[2,3]))", [2], "json"),
    "array_contains_all": ("array_contains_all([1,2,3],[1,3])", 1, "number"),
    "array_contains_seq": ("array_contains_seq([1,2,3],[2,3])", 1, "number"),
    "array_join": ("array_join([1,2],',')", [["1,2"]], "equals"),
    "array_filter": ("array_filter([1,2,3],[true,false,true])", [1, 3], "json"),
    "array_map": ("array_map(x -> x*2,[1,2,3])", [2, 4, 6], "json"),
    "array_repeat": ("array_repeat(7,3)", [7, 7, 7], "json"),
    "array_top_n": ("array_top_n([1,3,2],2)", [3, 2], "json"),
    "array_flatten": ("array_flatten([[1,2],[3]])", [1, 2, 3], "json"),
    "arrays_overlap": ("arrays_overlap([1,2],[2,3])", 1, "number"),
    "any_match": ("any_match([false,true])", 1, "number"),
    "all_match": ("all_match([true,true])", 1, "number"),
    "map_size": ("map_size(map{'a':1,'b':2})", 2, "number"),
    "map_keys": ("array_sort(map_keys(map{'a':1,'b':2}))", ["a", "b"], "json"),
    "map_values": ("array_sort(map_values(map{'a':1,'b':2}))", [1, 2], "json"),
    "map_from_arrays": ("map_from_arrays(['a','b'],[1,2])", {"a": 1, "b": 2}, "json"),
    "map_concat": ("map_concat(map{'a':1},map{'b':2})", {"a": 1, "b": 2}, "json"),
    "map_filter": ("map_filter(map{'a':1,'b':2},[true,false])", {"a": 1}, "json"),
    "map_apply": ("map_apply((k,v) -> (k,v+1),map{'a':1})", {"a": 2}, "json"),
    "json_query": ("json_query(parse_json('{\"a\":2}'),'$.a')", 2, "json"),
    "json_exists": ("json_exists(parse_json('{\"a\":2}'),'$.a')", 1, "number"),
    "json_contains": (
        "json_contains(parse_json('{\"a\":2}'),parse_json('{\"a\":2}'))",
        1,
        "number",
    ),
    "json_keys": ("json_keys(parse_json('{\"a\":2}'))", ["a"], "json"),
    "json_length": ("json_length(parse_json('[1,2,3]'))", 3, "number"),
    "json_remove": ("json_remove(parse_json('{\"a\":1,\"b\":2}'),'$.a')", {"b": 2}, "json"),
    "json_set": ("json_set(parse_json('{\"a\":1}'),'$.a',2)", {"a": 2}, "json"),
    "json_string": ("json_string(parse_json('{\"a\":1}'))", {"a": 1}, "json"),
    "parse_json": ("parse_json('{\"a\":1}')", {"a": 1}, "json"),
    "to_json": ("to_json(named_struct('a',1))", {"a": 1}, "json"),
    "str_to_map": ("str_to_map('a:1,b:2',',',':')", {"a": "1", "b": "2"}, "json"),
}


def complex_case(function: FunctionSignature) -> Case | None:
    name = function.name
    types = argument_types(function.signature)
    recipe = RECIPES.get(name)
    if name in {"array_contains", "array_position"} and len(types) == 2:
        element_type = types[1]
        value = "2024-01-02" if element_type in {"DATE", "DATETIME"} else 1
        element = typed(element_type, value)
        expression = f"{name}([{element}],{element})"
        recipe = expression, 1, "number"
    elif name == "array_slice":
        expression = "array_slice([1,2,3],2" + (",2" if len(types) == 3 else "") + ")"
        recipe = expression, [2, 3], "json"
    elif name == "array_join" and len(types) == 3:
        recipe = "array_join([1,NULL,2],',','x')", [["1,x,2"]], "equals"
    elif name in {"json_keys", "json_length"} and len(types) == 2:
        expression = f"{name}(parse_json('{{\"a\":{{\"b\":1}}}}'),'$.a')"
        recipe = expression, (["b"] if name == "json_keys" else 1), "json"
    elif name in {"json_array", "json_object"}:
        if not types:
            recipe = f"{name}()", ([] if name == "json_array" else {}), "json"
        elif name == "json_array":
            recipe = "json_array(1,'x',NULL)", [1, "x", None], "json"
        else:
            recipe = "json_object('a',1)", {"a": 1}, "json"
    elif name == "to_json" and types == ["ANY_MAP"]:
        recipe = "to_json(map{'a':CAST(1 AS BIGINT)})", {"a": 1}, "json"
    elif name.startswith("get_json_") and len(types) == 2:
        value = {"bool": True, "int": 7, "double": 1.5, "string": "x", "object": {"b": 2}}
        kind = name.removeprefix("get_json_")
        if kind not in value:
            return None
        argument = (
            typed(types[0], {"a": value[kind]})
            if types[0] == "JSON"
            else typed(types[0], __import__("json").dumps({"a": value[kind]}))
        )
        expected = value[kind]
        oracle = "json" if kind == "object" else "equals"
        recipe = (
            f"{name}({argument},'$.a')",
            (expected if oracle == "json" else [[expected]]),
            oracle,
        )
    if not recipe:
        return None
    expression, expected, oracle = recipe
    return Case(
        f"function.{function.key}",
        "complex",
        "SELECT " + expression,
        expected,
        oracle,
        signatures=() if "INVALID_TYPE" in types else (function.key,),
        contract_source=(
            "https://github.com/StarRocks/starrocks/blob/"
            "4a9848edf03f5c936dac664b2d52527f48e72eb0/gensrc/script/functions.py"
        ),
    )
