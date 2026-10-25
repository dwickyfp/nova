"""The calculation language for worksheets.

Formulas are parsed (:mod:`.syntax`), type- and level-checked
(:mod:`.analyzer`) against the sheet's columns and other calculated fields,
and lowered to StarRocks SQL (:mod:`.lower`) by the worksheet compiler.
"""

from app.modules.viz.calc.analyzer import (
    CalcInfo,
    DataType,
    Level,
    NamedCalcError,
    analyze_calculations,
    check_formula,
)
from app.modules.viz.calc.functions import FUNCTIONS, catalog
from app.modules.viz.calc.syntax import CalcError, parse

__all__ = [
    "FUNCTIONS",
    "CalcError",
    "CalcInfo",
    "DataType",
    "Level",
    "NamedCalcError",
    "analyze_calculations",
    "catalog",
    "check_formula",
    "parse",
]
