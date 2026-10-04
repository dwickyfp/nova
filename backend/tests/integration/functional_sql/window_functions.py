from .contracts import Case, EffectCheck, FunctionSignature
from .functions import argument_types, typed


def window_case(function: FunctionSignature) -> Case | None:
    name = function.name
    ranks = {
        "row_number": [[1], [2], [3], [4], [5], [6]],
        "rank": [[1], [2], [3], [4], [5], [6]],
        "dense_rank": [[1], [2], [3], [4], [5], [6]],
        "ntile": [[1], [1], [1], [2], [2], [2]],
        "cume_dist": [[1 / 6], [2 / 6], [3 / 6], [4 / 6], [5 / 6], [1.0]],
        "percent_rank": [[0.0], [0.2], [0.4], [0.6], [0.8], [1.0]],
    }
    if name in ranks:
        arguments = "2" if name == "ntile" else ""
        return Case(
            "function." + function.key,
            "analytic",
            f"SELECT {name}({arguments}) OVER(ORDER BY n) FROM numbers ORDER BY n",
            ranks[name],
            signatures=(function.key,),
            contract_source=(
                "https://github.com/StarRocks/starrocks/blob/"
                "4a9848edf03f5c936dac664b2d52527f48e72eb0/"
                "fe/fe-core/src/main/java/com/starrocks/sql/analyzer/AnalyticAnalyzer.java"
            ),
        )
    if name not in {"lag", "lead", "first_value", "last_value"}:
        return None
    types = argument_types(function.signature)
    sql_type = types[0]
    if sql_type in {"UNKNOWN_TYPE", "NULL_TYPE"}:
        return Case(
            "function." + function.key,
            "analytic",
            "",
            signatures=(function.key,),
            prerequisite=f"Catalog type {sql_type} needs a verified user expression",
        )
    value = (
        "2024-01-02"
        if sql_type in {"DATE", "DATETIME"}
        else "a"
        if sql_type in {"VARCHAR", "CHAR"}
        else 1
    )
    if sql_type == "JSON":
        value = {"a": 1}
    argument = "[1,2]" if sql_type == "INVALID_TYPE" else typed(sql_type, value)
    arguments = ["input_value"]
    if len(types) > 1:
        arguments.append("1")
    if len(types) > 2:
        arguments.append("NULL")
    frame = (
        " ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING"
        if name in {"first_value", "last_value"}
        else ""
    )
    expression = f"{name}({','.join(arguments)}) OVER(ORDER BY n{frame})"
    relation = f"(SELECT n,{argument} AS input_value FROM numbers) inputs"
    if sql_type == "PERCENTILE" or (
        sql_type in {"BITMAP", "HLL"} and name in {"first_value", "last_value"}
    ):
        return Case(
            "function." + function.key,
            "analytic",
            f"SELECT {expression} FROM {relation}",
            error_code=1064,
            error_contains=("percentile type" if sql_type == "PERCENTILE" else "type could only"),
            signatures=(function.key,),
            contract_source=(
                "https://github.com/StarRocks/starrocks/blob/"
                "4a9848edf03f5c936dac664b2d52527f48e72eb0/"
                "fe/fe-core/src/main/java/com/starrocks/sql/analyzer/AnalyticAnalyzer.java"
            ),
        )
    result = "v"
    expected, oracle = value, "number"
    if sql_type in {"DATE", "VARCHAR", "CHAR"}:
        expected, oracle = [[value]], "equals"
    elif sql_type == "DATETIME":
        expected, oracle = [[value + " 00:00:00"]], "equals"
    elif sql_type == "JSON":
        oracle = "json"
    elif sql_type == "INVALID_TYPE":
        expected, oracle = [1, 2], "json"
    elif sql_type.startswith("ARRAY<") or sql_type in {"ANY_ARRAY", "ANY_MAP", "ANY_STRUCT"}:
        from .aggregates import sample

        try:
            argument, expected, oracle = sample(sql_type)
        except ValueError:
            return None
        relation = f"(SELECT n,{argument} AS input_value FROM numbers) inputs"
    elif sql_type == "VARBINARY":
        result, expected, oracle = "hex(v)", [["31"]], "equals"
    elif sql_type == "BITMAP":
        result, expected = "bitmap_count(v)", 1
    elif sql_type == "HLL":
        result, expected = "hll_cardinality(v)", 1
    query = f"SELECT {result} FROM (SELECT n,{expression} AS v FROM {relation}) w"
    edge = 0 if name == "lag" else 5
    effects = ()
    if name in {"lag", "lead"}:
        effects = (
            EffectCheck(
                f"{query} WHERE n={edge}",
                [[0]] if sql_type == "BITMAP" else [[None]],
                "equals",
            ),
        )
    return Case(
        "function." + function.key,
        "analytic",
        f"{query} WHERE n=2",
        expected,
        oracle,
        signatures=() if sql_type == "INVALID_TYPE" else (function.key,),
        effects=effects,
        contract_source=(
            "https://github.com/StarRocks/starrocks/blob/"
            "4a9848edf03f5c936dac664b2d52527f48e72eb0/"
            "fe/fe-core/src/main/java/com/starrocks/sql/analyzer/AnalyticAnalyzer.java"
        ),
    )
