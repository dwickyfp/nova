# Nove UI action audit — 2026-09-23

## Scope

Nove can discover an exact Nova API operation and call it through the FastAPI app
as the signed-in user. The API still enforces the active role and its existing
business rules. Nove cannot provide an arbitrary URL. Writes require a separate
approval for each call, and the approval card shows the target and sanitized
input. A bounded loop permits up to four discovery calls and four action calls
per turn.

The catalog currently contains **283 of 301** documented `/api/v1` GET, POST,
PUT, PATCH, and DELETE operations. This is route coverage, not proof that every
operation can finish from a chat request. User creation now takes its password
in an uncontrolled approval-card field. It is sent only through the consent
decision and the existing Nova user API, outside model arguments and transcript.
Both stage upload routes take a browser-selected file through a separate,
owner-scoped multipart approval. The file is spooled for one call, capped at
256 MB, and never appears in the provider request or tool preview.

## Safety checks

| Check | Result |
| --- | --- |
| API authorization | In-process request uses the current session; representative role-gated request returned 403. A bridged attempt to delete `ACCOUNTADMIN` returned 400 under the existing immutable-role guard. |
| Mutation consent | Required per call, including under a read-only grant. |
| Audit | `PENDING` before a write, then `SUCCESS`, `FAILED`, or `UNKNOWN`; specialized agent and semantic-model creation also audit writes. |
| Credentials | Credential-shaped arguments are rejected before the API; output fields and result cells are redacted. |
| User creation | The owned pending call accepts exactly one secure password field. It is held in the request-local consent future, used once, and cleared before tool completion. |
| SQL write | One statement per approval, `confirm_destructive=true`, 100-row result cap, existing SQL guard and audit pipeline. Role switches use the dedicated session-aware tool. |
| Stage file paths | A nested filename is accepted for stage file deletion only after normalization; traversal, absolute paths, backslashes, and encoded path confusion are rejected. |
| Stage uploads | Both stage-ID and explorer-by-name routes use browser-selected multipart files, still pass the normal stage write-access gate, and reject cross-owner approval. Explorer uploads can name a normalized destination path. |
| Workspace state | Approval waits for unsaved local edits; successful file edits refresh the open tab, and successful deletes close it. If an edit arrives while local changes are open, automatic saves pause until the user copies the draft and reloads. |
| Semantic model grounding | A zero-row `SELECT` verifies the caller's active-role access, then table metadata is read through the same query pipeline; denied tables stop before provider generation. |

The 18 omitted operations are authentication/session endpoints (6), stage
binary download (1), password reset (1), internal ML prediction (1),
conversation detail reads (2), assistant grant changes (2), recursive message
submission (2), and tool-decision endpoints (3). Conversation
detail reads are excluded because they would feed another thread's messages into
the model. Stage download needs a browser handoff so binary content stays out
of the model context. Password reset currently returns a generated password in
its API response, so the generic bridge cannot safely finish that flow for the
user. Authenticated MCP connection is a broader Nova platform gap: the current
MCP form and API have no credential input or secret-reference field.

MCP server creation and discovery are in the catalog. The existing Nova MCP
feature stores server/tool metadata; it does not yet invoke discovered MCP tools.
The catalog does not turn that metadata into executable MCP capabilities.

## Retest and benchmark

- Focused backend retest after both stage upload routes: 37 passed across UI
  actions, secure approval, and evaluation trajectories; the authoring approval
  tests also passed earlier.
- Full backend unit sweep with loopback access: 3,616 passed, 3 warnings. Nine
  workspace version tests initially failed because their fake object body lacked
  the `close()` method used by the real storage stream. Adding that method to
  the fake made all 12 tests in that file pass; the full sweep then passed.
- Assistant behavior scorecard: 41/41 scenarios and 135/135 checks passed.
- Targeted frontend browser tests: 39 passed. TypeScript and the production
  frontend build passed.
- Offline bridge benchmark covers 200 search/preview samples and 50 in-process
  API calls. The latest isolated run measured a 4,391 ms cold catalog build,
  search median 1.573 ms/p95 11.659 ms, preview median 0.070 ms/p95 1.111 ms,
  and mocked API median 4.717 ms/p95 23.302 ms. Earlier runs were faster;
  concurrent machine load changed these timings substantially. These numbers
  exclude StarRocks and LLM latency.

The configured provider answered a minimal synthetic completion request. A full
Nove loop test against that external provider was stopped by automatic approval
review because it would transmit Nova's internal prompt, tool schemas, and
context to the provider host without destination-specific approval. Browser UI
automation was unavailable because the browser-control service failed to load
its request-header policy; the API and browser test harness were used instead.

## Remaining work for literal UI parity

1. Redesign password reset so the generated credential does not travel through
   an API response or model result; add a browser-only handoff or user-chosen
   password through secure approval.
2. Add a browser download handoff. Download bytes must stay outside the
   provider request. Uploaded files above 256 MB also need a streaming handoff
   that does not spool the whole file in one assistant worker.
3. Add a secure MCP credential reference to Nova's existing form/API if the
   target MCP server requires authentication. Implement MCP tool invocation if
   the intended capability includes actions offered by connected MCP servers,
   beyond server registration/discovery.
4. Run a real signed-in Nova UI session and full provider-driven Nove trajectory
   once browser access and destination-specific provider approval are available.

This audit does not claim 100% UI parity yet.
