# Smart Report delivery status

P0–P2 source is implemented in the original `feat/intelligent-studio-harness-v2`
checkout. The local acceptance gates below passed and the application has been
reloaded from that checkout. Delivery uses an upstream-only merge followed by a
selective feature commit on the same branch. These are local results; no GitHub
Actions result is claimed.

## Implemented behavior

- Published definitions, private editor drafts, explicit publish/restore, grants,
  append-only revisions, operation replay, scoped sessions and one-use handoff.
- Governed semantic execution, exact decimal transport, independent totals,
  calculation AST, fiscal/time comparisons, pivot and stable pagination.
- Reports library, shared chat/full/export renderer, native filters, contextual
  Ask Nova, manual/AI patch review, undo/redo and version history.
- Cross-filter, drill/group/detail, saved views, multi-page authoring, advanced
  ECharts/map/table adapters and evidence-based descriptive insights.
- Artifact copies and resumable dashboard migration. The actual inventory had
  one dashboard: one replacement draft and ledger, one mapping-required widget;
  repeat reconciliation created no duplicate and preserved the source checksum.
- All-page PDF/XLSX/PPTX snapshots in managed storage, reauthorization on download,
  validated templates, consented subscriptions and deterministic threshold alerts.
  Scheduled results and activity remain inside Nova.

## Latest local checks

| Gate | Latest result |
| --- | --- |
| Backend full unit/coverage | 7,584 passed; 61% coverage (401.01 s) |
| Report focused unit | 72 passed |
| Independent report engine | 7 passed; 11,037-row fixture and exact decimal regression |
| Agent eval | 48/48 scenarios, 165/165 checks; 54 affected trajectories passed (10.30 s) |
| Static/boundary | Full-app mypy, changed-file Ruff (109 Python files) and user-data boundary passed |
| Frontend | UI check, lint (21 warnings, zero errors), build, coverage: 1,416 passed across 167 files; 52.99% statements / 54.16% lines |
| Patched FE/Ranger report API | Five report cases (including timing) and all five governed Studio regressions passed |
| Real external-model Reports UI | Passed: private draft creation and governed rendering |
| Browser UI | Final prompt/SSE/replay/handoff plus expanded report/mobile-scroll journey: 2 passed in 193.60 s |
| Export visual review | Two-page PDF and advanced heatmap/waterfall/map snapshots inspected; no blank trailing page |
| Seeded integration | 225 passed, 24 prerequisite/optional skips, 29 non-engine deselections; 1,378.50 s including the long Intelligence scenario |
| Docker runtime | Final image afd3bc8fee24: all 567 Python source hashes match; offline Chromium rendered 3 segments and a 116,727-byte PDF |

The final backend unit/eval/static runs cover the current application source,
including export admission limits. The complete engine rerun covers the revised
Ranger-aware fixtures. A separate real HTTP export check verifies overload returns
429 without a manifest and that retry succeeds after capacity becomes available.

The broad unit suite uses a removable Linux helper because the host SciPy binary
cannot load. Seeded integration reuses `nova-smart-unlimited`; governed acceptance
reuses `docker`. No additional engine or development-data reset was used. Optional
skips, scripted-provider tests and actual provider/engine tests are kept separate
in [TEST_MATRIX.md](TEST_MATRIX.md). UI judgment is in [UI_JUDGE.md](UI_JUDGE.md).

## Delivery and recovery

Remote main was integrated at `3428c1ee`. Selective staging separates report work
from the user's existing unlimited-agent, ML routing, map and number-format edits.
The pre-merge checkout and index are retained in stash
`16bc738b9e074cca72ea2fcb28b7d820fe7738ba` as a recovery checkpoint. The originally
staged waiting-lifecycle test belongs to the user and is excluded from this feature.
Do not blindly stage the remaining checkout or drop the preservation stash.

The original backend is ready on port 8000. The live frontend is on port 5175
(`/studio/reports`) because another preview owns port 5174. The frontend's API
proxy reaches the backend and unauthenticated report requests return 401.
No feature worktree was created; the unrelated worktree and its processes remain
untouched. Ranger credential repair is complete and documented in LOCAL_RANGER.md;
credentials stay in ignored, owner-readable local files.
