from __future__ import annotations

from .contracts import FunctionSignature

# FunctionSet resolves the first identical argument list, without comparing returns.
SHADOWED_SCALARS = {
    50010: ("year(DATETIME)", "INT", 50009),
    50020: ("month(DATETIME)", "INT", 50019),
    50060: ("dayofmonth(DATETIME)", "INT", 50059),
    50061: ("day(DATETIME)", "INT", 50058),
    50070: ("hour(DATETIME)", "INT", 50069),
    50080: ("minute(DATETIME)", "INT", 50079),
    50090: ("second(DATETIME)", "INT", 50089),
    50300: ("unix_timestamp()", "INT", 50284),
    50301: ("unix_timestamp(DATETIME)", "INT", 50285),
    50302: ("unix_timestamp(DATE)", "INT", 50286),
    50303: ("unix_timestamp(VARCHAR,VARCHAR)", "INT", 50287),
    110000: ("get_json_int(VARCHAR,VARCHAR)", "INT", 110022),
    110012: ("get_json_int(JSON,VARCHAR)", "INT", 110023),
}


def public_exclusion(function: FunctionSignature) -> dict | None:
    from .inventory import SOURCE_ROOT

    compact = function.signature.replace(" ", "")
    name = function.name
    if function.kind == "Scalar" and function.fid == 0:
        promoted = (
            name in {"add", "subtract", "multiply"}
            and compact == name + "(TINYINT,TINYINT)" and function.return_type == "TINYINT"
        ) or (
            name in {"add", "subtract", "multiply"}
            and compact == name + "(FLOAT,FLOAT)" and function.return_type == "FLOAT"
        )
        if promoted:
            return {
                "classification": "internal operator definition without public binding",
                "reason": (
                    "The pinned arithmetic analyzer promotes TINYINT operands for +, -, and * "
                    "to SMALLINT, and normalizes FLOAT operands to DOUBLE before lookup. "
                    "These hidden operator definitions cannot be selected by those SQL operators."
                ),
                "public_path": "arithmetic expression with analyzer-promoted types",
                "sources": [
                    SOURCE_ROOT
                    + "/fe/fe-core/src/main/java/com/starrocks/catalog/ScalarFunction.java#L126",
                    SOURCE_ROOT
                    + "/fe/fe-core/src/main/java/com/starrocks/sql/analyzer/"
                    "ExpressionAnalyzer.java#L1992",
                    SOURCE_ROOT
                    + "/fe/fe-core/src/main/java/com/starrocks/sql/analyzer/"
                    "ExpressionAnalyzer.java#L2135",
                ],
            }
    definition = SHADOWED_SCALARS.get(function.fid)
    if definition is None or function.kind != "Scalar":
        return None
    signature, return_type, selected_fid = definition
    if (
        function.signature.replace(" ", "").casefold() != signature.casefold()
        or function.return_type != return_type
    ):
        return None
    return {
        "classification": "shadowed scalar definition",
        "reason": (
            "The pinned registry registers an earlier definition with identical arguments. "
            "Public overload resolution ignores return types and selects that earlier definition."
        ),
        "selected_fid": selected_fid,
        "sources": [
            SOURCE_ROOT + "/gensrc/script/functions.py",
            SOURCE_ROOT + "/fe/fe-core/src/main/java/com/starrocks/catalog/FunctionSet.java#L1115",
            SOURCE_ROOT + "/fe/fe-core/src/main/java/com/starrocks/catalog/Function.java#L618",
        ],
    }
