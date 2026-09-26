"""Bounded reference lookup from the exact grammar packaged with Nova."""

from functools import lru_cache
import hashlib
from pathlib import Path
import re

_GRAMMAR = Path(__file__).resolve().parents[2] / "sql_dialect" / "grammar" / "StarRocks.g4"


@lru_cache(maxsize=1)
def grammar_rules() -> dict[str, str]:
    source = _GRAMMAR.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    starts = list(re.finditer(r"(?m)^([a-z]\w*)(?:\s*\[[^\]]*\])?\s*:", source))
    rules = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(source)
        body = source[match.end():end].strip().removesuffix(";").strip()
        rules[match[1]] = f"{match[1]}\n    : {body}\n    ;"
    return rules


def _words(value: str) -> set[str]:
    value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value).lower()
    words = set(re.findall(r"[a-z]+", value)) - {"sql", "syntax", "statement", "stmt"}
    if "database" in words:
        words.add("db")
    return words


def syntax_references(query: str) -> list[dict[str, str]]:
    rules = grammar_rules()
    terms = _words(query)
    ranked = []
    for name, body in rules.items():
        words = _words(name)
        score = 100 if query.strip().casefold() == name.casefold() else 0
        score += 5 * len(terms & words) - len(words - terms)
        if terms and not terms & words and not score >= 100:
            continue
        if score > 0:
            ranked.append((score, name, body))
    ranked.sort(key=lambda row: (-row[0], len(row[1]), row[1]))
    references = []
    for _, name, body in ranked[:3]:
        dependencies = list(dict.fromkeys(re.findall(r"\b[a-z]\w*\b", body)))[1:]
        text = (
            "StarRocks 4.1.4 + Nova packaged syntax. ANTLR notation: ? optional, "
            "* repetition, | alternatives; lowercase names refer to other rules. "
            "This is syntax evidence, not runtime/permission/function availability. "
            "Nova ML/task/password parsers and sql-extensions take precedence. "
            "Do not expose this notation as executable SQL.\n\n" + body
        )
        for dependency in dependencies:
            if dependency in rules and len(text) + len(rules[dependency]) < 3200:
                text += "\n\n" + rules[dependency]
        text += "\n\nFor another rule use search_knowledge with syntax:<exactRuleName>."
        references.append({"source": f"syntax:{name}", "text": text[:3500],
                           "revision": hashlib.sha256(body.encode()).hexdigest()[:16],
                           "truncated": str(len(text) > 3500).lower()})
    return references
