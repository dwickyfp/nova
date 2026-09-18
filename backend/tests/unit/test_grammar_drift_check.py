"""Unit tests for the grammar drift guard (NOVA-54 PR 1 / PR 2).

The CI job exercises the CLI end-to-end; these pin the *semantics* of the guard
so a refactor cannot quietly weaken it:

* a clean vendored file passes,
* an unmarked edit outside a NOVA block fails (this is the `--fuzz=0`
  guarantee: there is no fuzzy application step, so anything Nova changes must
  sit inside a marker),
* an edit **inside** a NOVA block passes -- markers shield legitimate changes,
* a marker that is opened and not closed is a hard error, not a silent skip.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_grammar_drift.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_grammar_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


drift = _load_module()

UPSTREAM = [
    "// limitations under the License.",
    "grammar StarRocks;",
    "a : A ;",
    "b : B ;",
    "c : C ;",
]

# Same shape as the real vendored files: a fenced provenance header, then the
# upstream body. `strip_provenance_header` keys off the fence line.
_FENCE = "// " + "-" * 75


def _vendored(body: list[str]) -> list[str]:
    """Wrap an upstream-like body in the vendored header, as the real files do.

    The header fence is inserted between the upstream license line (kept, since
    it precedes the fence) and the body. ``body`` is everything after that
    license line.
    """
    return [
        UPSTREAM[0],
        _FENCE,
        "// Nova vendored copy -- DO NOT EDIT.",
        _FENCE,
        *body,
    ]


def test_clean_vendored_file_passes() -> None:
    vendored = _vendored(UPSTREAM[1:])
    stripped = drift.strip_provenance_header(vendored)
    assert drift.check_marked_changes_only(stripped, UPSTREAM) == []


def test_unmarked_insert_fails() -> None:
    vendored = _vendored(["grammar StarRocks;", "a : A ;", "// sneaky", "b : B ;", "c : C ;"])
    stripped = drift.strip_provenance_header(vendored)
    findings = drift.check_marked_changes_only(stripped, UPSTREAM)
    assert findings, "an unmarked insert must be reported"
    assert "insert" in findings[0]


def test_unmarked_replace_fails() -> None:
    vendored = _vendored(["grammar StarRocks;", "a : A ;", "b : B_REWRITTEN ;", "c : C ;"])
    stripped = drift.strip_provenance_header(vendored)
    findings = drift.check_marked_changes_only(stripped, UPSTREAM)
    assert findings, "an unmarked replace must be reported"


def test_edit_inside_a_nova_block_passes() -> None:
    # The block *replaces* upstream `a : A ;` with a different rule. After the
    # block is stripped, the vendored file is upstream minus `a : A ;` -- a
    # deletion, which the guard allows.
    vendored = _vendored(
        [
            "grammar StarRocks;",
            "// NOVA-BEGIN (reason)",
            "nova_rule : X ;",
            "// NOVA-END",
            "b : B ;",
            "c : C ;",
        ]
    )
    without_blocks = drift.strip_nova_blocks("\n".join(vendored), keep_upstream=False)
    stripped = drift.strip_provenance_header(without_blocks)
    assert drift.check_marked_changes_only(stripped, UPSTREAM) == []


def test_unterminated_nova_block_is_an_error() -> None:
    text = "grammar StarRocks;\n// NOVA-BEGIN (reason)\nnova_rule : X ;\n"
    try:
        drift.strip_nova_blocks(text, keep_upstream=False)
    except ValueError as exc:
        assert "unterminated" in str(exc)
    else:  # pragma: no cover - the assertion below is the point
        raise AssertionError("an unterminated NOVA block must raise")


def test_marker_detection_ignores_prose_that_names_the_markers() -> None:
    # The provenance header says "NOVA-BEGIN/NOVA-END" in prose; that must not be
    # read as a marker, or the header would be swallowed as a block.
    lines = [
        "// Nova edits are wrapped in `NOVA-BEGIN`/`NOVA-END` markers.",
        "grammar StarRocks;",
    ]
    assert drift.marked_regions(lines) == []


def test_engine_pin_is_the_4_1_4_commit() -> None:
    # NOVA-51: the pin is a recorded commit SHA, not a moving tag. The vendored
    # headers and the drift-check constant must agree on the 4.1.4 commit.
    assert drift.EXPECTED_UPSTREAM_COMMIT == "4a9848edf03f5c936dac664b2d52527f48e72eb0"

    grammar_dir = SCRIPT.resolve().parents[2] / "backend" / "app" / "sql_dialect" / "grammar"
    for name in ("StarRocks.g4", "StarRocksLex.g4"):
        header = drift.read_header((grammar_dir / name).read_text(encoding="utf-8"))
        assert header["commit"] == drift.EXPECTED_UPSTREAM_COMMIT, name
        assert header["tag"].startswith("4.1.4"), name

