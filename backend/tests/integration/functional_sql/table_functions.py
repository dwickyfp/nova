from .contracts import Case, FunctionSignature
from .functions import argument_types, typed


def table_case(function: FunctionSignature) -> Case | None:
    types = argument_types(function.signature)
    if function.name == "generate_series":
        arguments = ",".join(
            typed(sql_type, value) for sql_type, value in zip(types, (1, 3, 1), strict=True)
        )
        sql = f"SELECT generate_series FROM TABLE(generate_series({arguments})) ORDER BY 1"
        expected = [["1"], ["2"], ["3"]] if types[0] == "LARGEINT" else [[1], [2], [3]]
    elif function.name == "unnest":
        sql, expected = (
            "SELECT value FROM numbers, unnest([1,2]) AS item(value) WHERE n=0",
            [
                [1],
                [2],
            ],
        )
    elif function.name == "json_each":
        sql = (
            'SELECT `key`,value FROM numbers,json_each(parse_json(\'{"a":1,"b":2}\')) '
            "WHERE n=0 ORDER BY `key`"
        )
        expected = [["a", "1"], ["b", "2"]]
    elif function.name == "unnest_bitmap":
        sql = (
            "SELECT value FROM numbers,unnest_bitmap(bitmap_from_string('1,2')) "
            "AS item(value) WHERE n=0 ORDER BY value"
        )
        expected = [[1], [2]]
    elif function.name == "subdivide_bitmap":
        sql = (
            "SELECT bitmap_count(value) FROM numbers, "
            f"subdivide_bitmap(bitmap_from_string('1,2,3'),{typed(types[1], 2)}) "
            "AS item(value) WHERE n=0 ORDER BY 1"
        )
        expected = [[1], [2]]
    else:
        return None
    return Case(
        "function." + function.key,
        "table",
        sql,
        expected,
        signatures=() if "INVALID_TYPE" in types else (function.key,),
    )
