# Corpus v3 changelog

## 2026-11-25.3: converted from corpus v2 (2026-11-24.2)

Mechanical: every v2 unit becomes a typed expectation (`v2/expectation.py`). `gt.numeric` modes become `required_measures` (all numeric result columns, one `col:` column, or `rows: first`); `expect_ui` becomes enforced `ui.kinds` (prose, follow-ups and 'number' stay with the judge); typed-in `expect_numbers` are removed (MCP units keep the governed part as a reference).

Reviewed corrections:

- kpi-008: E1: the user asked for revenue and order count; AOV was not requested.
- layout_page-004, layout_page-007, layout_page-008, session-038.t3, session-038.t3.fresh: E1: a June vs July page states both months; the grand total was not requested.
- table-014: E1: a list of non-completed orders is identified by order id; amounts not requested.
- session-009.t3, session-009.t3.fresh: E1: the top city by revenue is whatever session-009.t1's reference returns first (Yogyakarta), not a hard-coded Jakarta; the row is bound, not typed.
- what_if-006: E1: the old figure was July's actual revenue, not a target; grade the live control.
- typo-006: E1: 'brp org' reads as orders or as distinct customers; both readings are accepted.
- deep_research-001, deep_research-002, deep_research-003: E1: the old figures (overall revenue/orders/AOV) did not test the drivers asked for.
- automation-001, automation-002, automation-003, automation-004, session-025.t2, session-025.t2.fresh, session-026.t2, session-026.t2.fresh: D4 (owner, 2026-11-24): Studio is autonomous; a schedule or alert request runs and the turn ends in action_done (corpus_patch session-025.t2).
- session-005.t5, session-005.t5.fresh, session-006.t4, session-006.t4.fresh, session-037.t3, session-037.t3.fresh: D3 (owner): there is no save-from-chat tool; the expectation is a limitation that points to the Save button (save.suggested).
- session-006.t3: D5 (owner): an answer restated from history as a markdown table fails UI.
- number-026, multi_step-006: deferred until the semantic model has customer_count (FIX-PLAN PR-5, S1); linter rule 3.
