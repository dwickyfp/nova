"""No natural-language word lists or regexes in the agent code.

Understanding a question is the model's job, in any language; code checks the
model's structured output. A regex or word set that matches English or
Indonesian words silently fails every other language, so this test finds them.

``REMAINING`` held the files still being migrated; it is empty and stays so.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOTS = ("app/modules/assistant", "app/modules/agents", "app/modules/intelligence")

#: Words that only appear in a pattern meant to read people's sentences.
MARKERS = re.compile(
    r"\b(?:months?|bulan|years?|tahun|weeks?|minggu|quarters?|kuartal|why|kenapa|mengapa|"
    r"percent|persen|millions?|juta|billions?|miliar|rose|fell|naik|turun|teratas|terbesar|"
    r"highest|lowest|setiap|dibanding|compared|trend|tren|terakhir|yesterday|kemarin|today)\b",
    re.I,
)

_REGEX_CALLS = {"compile", "search", "match", "fullmatch", "sub", "findall", "finditer", "split"}

#: Canonical grain tokens are plan vocabulary, not sentences.
_GRAIN_TOKENS = {"hour", "day", "week", "month", "quarter", "year"}

#: Files still holding language patterns; each one is removed as it migrates.
#: Empty since NOVA-124 moved all language reading to the model. Keep it empty.
REMAINING: set[str] = set()


def _strings(node: ast.AST) -> list[str]:
    return [
        item.value for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]


def _identifier_vocabularies(tree: ast.AST) -> set[int]:
    """Nodes under ``X_IDENTIFIERS = ...``: schema column-name words, not user language."""
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id.endswith("_IDENTIFIERS")
            for target in node.targets
        ):
            skipped.update(id(child) for child in ast.walk(node.value))
    return skipped


def _language_patterns(tree: ast.AST) -> list[str]:
    found = []
    skipped = _identifier_vocabularies(tree)
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        candidates: list[str] = []
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "re"
            and node.func.attr in _REGEX_CALLS and node.args
        ):
            candidates = _strings(node.args[0])
        elif isinstance(node, (ast.Set, ast.List, ast.Tuple)) and len(node.elts) >= 3:
            # A literal collection of plain words is a word list.
            words = [
                item.value for item in node.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
            if (
                len(words) == len(node.elts)
                and all(re.fullmatch(r"[a-z ]+", word) for word in words)
                and not set(words) <= _GRAIN_TOKENS
            ):
                candidates = words
        found.extend(text for text in candidates if MARKERS.search(text))
    return found


def offenders() -> dict[str, list[str]]:
    result = {}
    for root in ROOTS:
        for path in sorted(Path(root).rglob("*.py")):
            hits = _language_patterns(ast.parse(path.read_text(), filename=str(path)))
            if hits:
                result[str(path)] = hits
    return result


def test_no_new_file_reads_language_with_regex():
    new = sorted(set(offenders()) - REMAINING)
    assert not new, f"Language regex or word list added in: {new}. Use the intent frame."


def test_migrated_files_leave_the_list():
    done = sorted(REMAINING - set(offenders()))
    assert not done, f"Remove from REMAINING, these no longer read language: {done}"
