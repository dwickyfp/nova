# Nove capability boundary

> Nove invokes bounded Nova capabilities, not HTTP routes selected from OpenAPI.

## Finding

The former `find_ui_operation` catalog exposed 322 authenticated UI operations
(139 GET, 115 POST, 42 DELETE, 13 PUT, 13 PATCH). `call_ui_operation` then sent
requests to FastAPI through an in-process ASGI transport with the user's Nova
session. This retained endpoint authorization and per-write consent, but gave
the model a broad, changing operation surface and displayed internal route
names and request shapes in conversation traces. Knowing a route is not an
authorization bypass; the model-controlled breadth and generic argument shape
are the risks addressed here.

The default Nove registry no longer registers either generic operation tool.
Nove cannot invoke the old bridge, even if the model proposes its name. The
legacy module remains for existing isolated tests and is not a runtime tool.

## Current replacement

`inspect_role_access` checks the exact existing role against Ranger policies.
`grant_role_access` applies up to 16 explicitly previewed database or table
permissions through `AccessControlService`, without HTTP. Both validate the
role and resources, require a live security-admin session, and audit use.
Grants are always classified as writes and require per-call approval. They may
partially apply if a later Ranger write fails; the result reports the count and
requires inspection before retrying. A successful request remains
`PROPAGATING` until Verify Access confirms it.

Read-only SQL continues through `query_execute` on the caller's connection.
The assistant may author SQL for a human to run where no dedicated write tool
exists. It must not invent a SQL form for an operation that Nova does not
support.

## Migration map

| UI domain | Preferred Nove capability | Status |
| --- | --- | --- |
| Agent role access | Typed Ranger inspect/grant tools | Available |
| Data reads and schema inspection | Caller-scoped `query_execute` | Available |
| Agent and semantic-model creation | Existing typed authoring tools | Available |
| Role membership and user lifecycle | Typed identity tools with secure input where needed | Pending |
| Data scopes, masks, and governance | Typed Ranger policy tools | Pending |
| Workspace files, stages, uploads | Typed storage tools with browser file handoff | Pending |
| ML, tasks, monitoring, and configuration | Domain service tools; SQL only where Nova implements it | Pending |

Pending domains are unavailable to Nove after removal of the generic bridge.
The UI and its authenticated APIs still function. Do not restore a generic
route-calling fallback to fill these gaps. Each new tool needs a bounded schema,
caller/role checks, consent classification, audit entry, redacted preview, and
a scripted trajectory in `tests/eval/`.
