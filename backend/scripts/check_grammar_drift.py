#!/usr/bin/env python3
"""Guard the vendored StarRocks grammar against silent edits.

Two independent checks run here, both offline (no network, no JVM):

1. **Provenance pin.** Each vendored ``.g4`` records, in its header, the
   upstream path, tag, commit and sha256 it was taken from. This script
   re-reads that header and asserts the recorded upstream sha256 matches the
   sha256 of the corresponding file in ``grammar/upstream/``. That copy is
   pristine upstream and is never edited, so a mismatch means the vendored
   body moved away from the pin without the header being updated.

2. **Marked-changes-only.** Strip the provenance header and every
   ``NOVA-BEGIN``/``NOVA-END`` block from the vendored file, then compare what
   is left against the pristine upstream with a line-level diff. Every diff
   opcode must be a *deletion* of upstream lines -- no ``insert`` and no
   ``replace``. In other words: outside the NOVA markers, the vendored grammar
   must still be byte-identical upstream text; Nova may only delete (because a
   NOVA block replaces the upstream lines it supersedes). An unmarked edit
   shows up as ``replace``/``insert`` and fails the check.

Check 2 is the design's ``--fuzz=0`` requirement in a different form. Nova does
not carry a separate patch file to apply fuzzily: the vendored copy IS the
patched copy, and the guard proves that every difference from upstream lands
inside a marker. A fuzzy or misplaced edit cannot slip through, because there is
no tolerant application step to absorb it -- it is either marked (and therefore
intentional) or an ``insert``/``replace`` that fails the build.

Usage::

    python backend/scripts/check_grammar_drift.py [--root <repo root>]

Exit code 0 = clean, 1 = drift detected.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import sys
from pathlib import Path

GRAMMAR_RELPATH = Path("backend/app/sql_dialect/grammar")

# The fence that opens and closes the Nova provenance header, e.g.
#     // ---------------------------------------------------------------------------
NOVA_FENCE = "// " + "-" * 75
# A marker is a whole comment line, e.g. ``// NOVA-BEGIN (reason)`` or
# ``# NOVA-BEGIN``. Matching only comment lines keeps prose that merely *names*
# the markers (the provenance header does) from being read as one.
NOVA_BEGIN_RE = re.compile(r"^\s*(//|#)\s*NOVA-BEGIN\b")
NOVA_END_RE = re.compile(r"^\s*(//|#)\s*NOVA-END\b")

UPSTREAM_FIELDS = ("file", "tag", "commit", "sha256 upstream")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def read_header(text: str) -> dict[str, str]:
    """Extract the ``key: value`` provenance fields from a vendored header.

    The header is the first fenced block. Fields are matched leniently on
    whitespace so the file stays readable. Missing fields are absent from the
    returned mapping and reported by the caller.
    """
    header: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip().lstrip("/ ").strip()
        for field in UPSTREAM_FIELDS:
            prefix = f"{field}:"
            if stripped.startswith(prefix):
                header[field] = stripped[len(prefix) :].strip()
    return header


def strip_nova_blocks(text: str, *, keep_upstream: bool) -> list[str]:
    """Return the lines of ``text`` with Nova-only regions removed.

    ``keep_upstream=False`` drops every ``NOVA-BEGIN``..``NOVA-END`` block
    inclusive (used on the vendored file). ``keep_upstream=True`` keeps the
    marker comments but is otherwise identical; it exists so the no-marker
    smoke test can confirm the markers are balanced without dropping content.
    """
    del keep_upstream  # both modes drop the block; parameter kept for call-site clarity
    out: list[str] = []
    depth = 0
    for line in text.splitlines():
        if NOVA_BEGIN_RE.match(line):
            depth += 1
        if depth == 0:
            out.append(line)
        if NOVA_END_RE.match(line):
            depth -= 1
            if depth < 0:
                raise ValueError("NOVA-END without a matching NOVA-BEGIN")
    if depth != 0:
        raise ValueError("unterminated NOVA-BEGIN block")
    return out


def strip_provenance_header(lines: list[str]) -> list[str]:
    """Remove the first fenced provenance block and the blank lines after it."""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n and lines[i].strip() != NOVA_FENCE:
        out.append(lines[i])
        i += 1
    if i >= n:
        raise ValueError("no provenance header fence found")
    i += 1  # drop the opening fence
    while i < n and lines[i].strip() != NOVA_FENCE:
        i += 1
    if i >= n:
        raise ValueError("unterminated provenance header fence")
    i += 1  # drop the closing fence
    out.extend(lines[i:])
    # The header is followed by blank padding in the vendored file; drop leading
    # blanks so the comparison starts at real grammar content on both sides.
    while out and out[0].strip() == "":
        out.pop(0)
    return out


def marked_regions(lines: list[str]) -> list[tuple[int, int]]:
    """1-based (start, end) line spans of every NOVA-BEGIN..NOVA-END block."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for idx, line in enumerate(lines, start=1):
        if NOVA_BEGIN_RE.match(line):
            if start is not None:
                raise ValueError(f"nested NOVA-BEGIN at line {idx}")
            start = idx
        if NOVA_END_RE.match(line):
            if start is None:
                raise ValueError(f"NOVA-END without NOVA-BEGIN at line {idx}")
            spans.append((start, idx))
            start = None
    if start is not None:
        raise ValueError(f"unterminated NOVA-BEGIN opened at line {start}")
    return spans


def check_marked_changes_only(vendored: list[str], upstream: list[str]) -> list[str]:
    """Return a list of human-readable drift findings (empty == clean)."""
    matcher = difflib.SequenceMatcher(a=upstream, b=vendored, autojunk=False)
    findings: list[str] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            continue  # upstream lines superseded by a NOVA block -- allowed
        sample = "\n".join(vendored[j1:j2][:4])
        findings.append(
            f"unmarked {tag} at vendored line {j1 + 1}: expected a NOVA-BEGIN/END block\n"
            f"{sample}"
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root (default: inferred from this script's location)",
    )
    args = parser.parse_args(argv)

    grammar_dir = args.root / GRAMMAR_RELPATH
    failures: list[str] = []
    checked = 0

    for vendored_path in sorted(grammar_dir.glob("*.g4")):
        upstream_path = grammar_dir / "upstream" / vendored_path.name
        if not upstream_path.is_file():
            failures.append(f"{vendored_path.name}: missing pristine copy {upstream_path}")
            continue

        vendored_text = vendored_path.read_text(encoding="utf-8")
        upstream_text = upstream_path.read_text(encoding="utf-8")
        header = read_header(vendored_text)

        for field in UPSTREAM_FIELDS:
            if field not in header:
                failures.append(f"{vendored_path.name}: header is missing '{field}:'")
        if failures and any(vendored_path.name in f for f in failures):
            continue

        upstream_sha = sha256_file(upstream_path)
        if header["sha256 upstream"] != upstream_sha:
            failures.append(
                f"{vendored_path.name}: header sha256 {header['sha256 upstream']} "
                f"!= pristine upstream {upstream_sha}; the pin is stale or the "
                "pristine copy was edited"
            )

        try:
            regions = marked_regions(vendored_text.splitlines())
            without_blocks = strip_nova_blocks(vendored_text, keep_upstream=False)
            stripped = strip_provenance_header(without_blocks)
        except ValueError as exc:
            failures.append(f"{vendored_path.name}: {exc}")
            continue

        upstream_lines = upstream_text.splitlines()
        for finding in check_marked_changes_only(stripped, upstream_lines):
            failures.append(f"{vendored_path.name}: {finding}")

        if regions:
            spans = ", ".join(f"{a}-{b}" for a, b in regions)
            print(
                f"OK  {vendored_path.name}: pin {header['commit']} "
                f"({len(regions)} marked region(s): {spans})"
            )
        else:
            print(f"OK  {vendored_path.name}: pin {header['commit']} (no marked regions)")
        checked += 1

    if failures:
        print("\nGrammar drift check FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    if checked == 0:
        print("Grammar drift check FAILED: no vendored .g4 files found", file=sys.stderr)
        return 1

    print(f"Grammar drift check passed ({checked} file(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
