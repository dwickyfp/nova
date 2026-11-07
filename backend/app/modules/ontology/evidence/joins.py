"""Find join paths from data, the way Auto-BI does: inclusion, keys, names, one target each.

A column A.x references B.y when every distinct value of A.x occurs in B.y
(inclusion), B.y identifies the rows of B (a key), and names agree or the data
alone is unambiguous. Discovery filters with KMV sketches and then verifies the
survivors exactly (filter-then-verify), so neither the HLL error of a key's
distinct count nor the sketch error of a small column in a large one decides.

Rules that follow from the measured errors on the lab databases:

* a key is the table's identifier: a unique column named as one (``*_id``,
  ``*_code``), or the only unique column of its table; a unique ``city`` in a
  four-row ``warehouses`` table is an attribute, not something to join to;
* a column that identifies its own table (``event_id`` in ``fulfillment_events``)
  is not a reference to another one, while ``customer_id`` in
  ``customer_health_events`` names a modifier and still refers to customers;
* a column named after the target table (``order_id`` and ``orders``) outranks
  another key with the same column name (``payment_attempts.order_id``);
* a role (``salesman_id`` to ``employees.employee_id``) has no name evidence,
  so text keys with full inclusion qualify on data alone, while small whole
  numbers, which collide by chance, need a name;
* a one-row dimension (one campaign) still joins when the name says so.

Each source column keeps at most one target. Every candidate keeps its
features so that a calibrated model can replace the rule (Phase C) and a
steward can see why a join was proposed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any

from app.modules.ontology.evidence.profiling import ColumnProfile, Sketch, containment

#: A key repeats at most 2% of its non-null values (dirty data), once verified exactly.
KEY_UNIQUENESS = 0.98
#: HLL distinct counts err by a few percent; this lets a probable key reach verification.
PROBABLE_KEY = 0.95
#: Verified inclusion a join needs; below this the source has values the target lacks.
MIN_CONTAINMENT = 0.9
#: Inclusion a join without name evidence needs.
NAMELESS_CONTAINMENT = 0.99
#: Distinct values a nameless text join needs, so a two-value flag is not a key.
NAMELESS_MIN_NDV = 5
#: Sketch inclusion that sends a pair to exact verification.
CANDIDATE_CONTAINMENT = 0.3

_SPLIT = re.compile(r"[_\W]+|(?<=[a-z0-9])(?=[A-Z])")
_KEY_WORDS = {"id", "key", "code", "no", "nr", "num", "number", "sk", "fk", "pk"}


def tokens(name: str) -> set[str]:
    return {part.lower() for part in _SPLIT.split(name) if part}


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _subject(name: str) -> set[str]:
    """What a column or table name is about, without key words or plurals."""
    return {_singular(word) for word in tokens(name)} - _KEY_WORDS


def is_key_named(column: str) -> bool:
    return bool(tokens(column) & _KEY_WORDS)


def names_table(column: str, table: str) -> bool:
    """``outlet_id`` names ``outlets``; ``plan_id`` names ``route_plans``."""
    subject, table_subject = _subject(column), _subject(table)
    return bool(subject) and is_key_named(column) and subject <= table_subject


def identifies_table(column: str, table: str) -> bool:
    """``event_id`` identifies ``fulfillment_events``: it names the table's head noun (English
    table names end with it) or the whole table. ``customer_id`` in ``customer_health_events``
    names a modifier, so it refers to customers rather than identifying health events."""
    subject = _subject(column)
    words = [_singular(word) for word in _SPLIT.split(table) if word]
    words = [word.lower() for word in words if word.lower() not in _KEY_WORDS]
    if not subject or not is_key_named(column) or not words:
        return False
    return subject == {words[-1]} or subject == set(words)


@dataclass(frozen=True)
class JoinCandidate:
    source: str  # database.table.column
    target: str
    containment: float
    reverse_containment: float
    target_uniqueness: float
    source_uniqueness: float
    source_ndv: int
    target_ndv: int
    family: str
    same_name: bool
    names_target_table: bool
    name_similarity: float
    lengths_overlap: bool
    target_is_identifier: bool
    source_is_own_identifier: bool
    verified: bool = False

    @property
    def name_evidence(self) -> bool:
        return self.same_name or self.names_target_table

    def accepted(self) -> bool:
        """The Phase B decision rule; Phase C replaces it with a calibrated probability."""
        if self.source_is_own_identifier or not self.target_is_identifier:
            return False
        if self.containment < MIN_CONTAINMENT or self.target_uniqueness < KEY_UNIQUENESS:
            return False
        if self.names_target_table:
            return True
        if self.source_ndv < 2 or self.target_ndv < 2:
            return False
        if self.same_name:
            return True
        return (
            self.family == "text"
            and self.lengths_overlap
            and self.containment >= NAMELESS_CONTAINMENT
            and self.source_ndv >= NAMELESS_MIN_NDV
        )

    def rank(self) -> tuple:
        return (self.names_target_table, self.same_name, round(self.containment, 3),
                self.name_similarity, -self.target_ndv)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "name_evidence": self.name_evidence}


def identifier_columns(profiles: Iterable[ColumnProfile]) -> set[str]:
    """Columns that identify their table's rows: a probably unique id-named column, or the
    only probably unique column of a table that has no id-named one."""
    by_table: dict[tuple[str, str], list[ColumnProfile]] = {}
    for item in profiles:
        if item.family and item.ndv >= 1 and item.uniqueness >= PROBABLE_KEY:
            by_table.setdefault((item.database, item.table), []).append(item)
    found: set[str] = set()
    for unique in by_table.values():
        named = [item for item in unique if is_key_named(item.column)]
        if named:
            found |= {item.key for item in named}
        elif len(unique) == 1:
            found.add(unique[0].key)
    return found


def candidates(
    profiles: Iterable[ColumnProfile], sketches: dict[str, Sketch]
) -> list[JoinCandidate]:
    """Every source → probable key pair of the same kind of value with some sketch inclusion."""
    columns = [item for item in profiles if item.family and item.key in sketches]
    identifiers = identifier_columns(columns)
    keys = [item for item in columns if item.key in identifiers]
    found = []
    for source in columns:
        if source.ndv < 1:
            continue
        own = source.uniqueness >= PROBABLE_KEY and identifies_table(source.column, source.table)
        for target in keys:
            if (target.database, target.table) == (source.database, source.table):
                continue
            if target.family != source.family:
                continue
            share = containment(sketches[source.key], sketches[target.key])
            if share < CANDIDATE_CONTAINMENT:
                continue
            a, b = tokens(source.column), tokens(target.column)
            union = a | b
            overlap = (
                source.min_length is not None and target.min_length is not None
                and source.max_length is not None and target.max_length is not None
                and source.min_length <= target.max_length
                and target.min_length <= source.max_length
            )
            found.append(JoinCandidate(
                source=source.key, target=target.key, containment=round(share, 4),
                reverse_containment=round(
                    containment(sketches[target.key], sketches[source.key]), 4),
                target_uniqueness=round(target.uniqueness, 4),
                source_uniqueness=round(source.uniqueness, 4),
                source_ndv=source.ndv, target_ndv=target.ndv, family=str(source.family),
                same_name=source.column.lower() == target.column.lower()
                and is_key_named(source.column),
                names_target_table=names_table(source.column, target.table),
                name_similarity=round(len(a & b) / len(union), 3) if union else 0.0,
                lengths_overlap=overlap,
                target_is_identifier=True,
                source_is_own_identifier=own,
            ))
    return found


def verify(
    item: JoinCandidate, *, matched: int, distinct: int, target_non_null: int,
    target_distinct: int,
) -> JoinCandidate:
    """The candidate with exact inclusion and key uniqueness in place of the estimates."""
    return replace(
        item,
        containment=round(matched / distinct, 4) if distinct else 0.0,
        source_ndv=distinct,
        target_ndv=target_distinct,
        target_uniqueness=round(target_distinct / target_non_null, 4) if target_non_null else 0.0,
        verified=True,
    )


def worth_verifying(item: JoinCandidate) -> bool:
    """Pairs the rule could accept once their estimates are exact."""
    return not item.source_is_own_identifier and item.target_is_identifier


def select(found: list[JoinCandidate]) -> list[JoinCandidate]:
    """Accepted joins, one target per source column; a key never points back at its referrer."""
    best: dict[str, JoinCandidate] = {}
    for item in found:
        if not item.accepted():
            continue
        current = best.get(item.source)
        if current is None or item.rank() > current.rank():
            best[item.source] = item
    chosen = list(best.values())
    pairs = {(item.source, item.target) for item in chosen}
    # Two keys that contain each other (a 1:1 pair) keep one direction: from the less
    # unique side, else by name.
    return sorted(
        (item for item in chosen
         if not ((item.target, item.source) in pairs
                 and (item.source_uniqueness, item.source) > (item.target_uniqueness,
                                                              item.target))),
        key=lambda item: (item.source, item.target),
    )


def score(
    predicted: Iterable[tuple[str, str]], gold: Iterable[tuple[str, str]]
) -> dict[str, Any]:
    """Precision, recall and F1 of directed joins; a pair found reversed counts as found."""
    from app.common.statistics import proportion

    predicted_set = set(predicted)
    gold_set = set(gold)

    def hit(pair: tuple[str, str], pool: set[tuple[str, str]]) -> bool:
        return pair in pool or (pair[1], pair[0]) in pool

    true_positive = sum(1 for pair in predicted_set if hit(pair, gold_set))
    found = sum(1 for pair in gold_set if hit(pair, predicted_set))
    precision = proportion(true_positive, len(predicted_set))
    recall = proportion(found, len(gold_set))
    p, r = precision["value"] or 0.0, recall["value"] or 0.0
    return {
        "precision": precision, "recall": recall,
        "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
        "predicted": len(predicted_set), "gold": len(gold_set),
        "false_positives": sorted(pair for pair in predicted_set if not hit(pair, gold_set)),
        "missed": sorted(pair for pair in gold_set if not hit(pair, predicted_set)),
    }
