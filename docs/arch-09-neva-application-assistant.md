# Architecture 9: Neva Application Copilot

> Neva uses the active Nova page, its safe actions, and recent outcomes to help users complete platform tasks.

---

## Architecture

```text
Nova page → useNevaSurface → AssistantProvider → app_context → Neva turn
   │              │                 │                             │
   │         typed actions       recent events               planner/tools
   │              ↑                 ↑                             │
   └──── action outcome ───────────┘ ← client_action SSE ←───────┘
```

Neva is Nova's platform copilot. It helps users navigate, inspect, configure, and troubleshoot Nova. A Nova Studio agent is a user-created agent with its own domain task, configuration, and conversation. Neva can inspect the Studio configuration while the user edits it; it does not use the Studio agent as its own runtime or read its conversation.

The existing assistant loop, provider, consent broker, SQL tools, knowledge search, and generic `list_ui_operations`/`call_ui_operation` bridge remain in place. Application context adds a current-page observation to each turn. Typed client actions serve frequent UI workflows; the generic API bridge remains available for less common supported operations.

## Implementation

### Application context

The assistant message request accepts an optional `app_context` alongside legacy `database`, `schema`, and `role` fields. Version 1 has named `surface`, `entity`, `selection`, `editor`, `execution`, `view`, `domain`, `capabilities`, and recent `events` fields. The frontend builds the envelope from the active surface immediately before a turn. Both ends bound strings, lists, metadata, and total serialized size. Free-form values containing credential markers are omitted or replaced as whole fields before they enter model context, including page search text and event payloads.

Current selection, entity, and execution evidence take precedence when Neva resolves references such as “this role” or “fix it”. Events for another surface, workspace document, or entity are excluded from the current reference set. A client without `app_context` continues through the existing assistant request path.

The client's `domain.role` describes what the page displayed. The authenticated StarRocks session remains the authority for execution and authorization. A page cannot grant itself privileges by changing its context envelope.

### Turn harness

Neva composes the shared `AssistantLoop` with Studio's analyst limits (16
steps, 180 s, iterative re-planning, history summarization, spend limit); once
the plan shows a quick turn (explain, help, catalog, a question back) the
limits drop to Studio's fast profile (8 steps, 60 s). A plan the model cannot
shape becomes a read-only plan. Read-only checks the model skipped (the guide,
the editor's failed query, the SQL its draft proposes) are run by the loop
before the answer; a failed lookup that is not an access refusal is corrected
once; anything still unmet ends in a plain "I won't guess" message, never an
empty reply or an engine error.

Files attached in the panel (up to 3: text, PDF or image) reach the model as
data, are kept with the message and audited. The "always allow read-only"
grant is kept per user and thread in Redis, stamped with the active role and
session version; another context never inherits it. Durable facts the person
states are remembered per user and role (Studio's memory store, system id
`neva-assistant`), listed and removed through `/memories`, and given to the
model as untrusted data.

### Active role

Nova is role-based: every Neva turn runs as the one active role of the user's web session, the role picked in the account menu. The request's `role` field is ignored. Neva's model context names that role and the user's assigned roles, and every query, governed metric and Semantic View lookup uses it.

When a check is denied and another assigned role may allow it, Neva offers `USE ROLE <role>` (or `SET ROLE`) as its own statement. A role switch:

- always asks for approval, separately from any "always allow read-only" grant, and that grant is cleared afterwards;
- goes through the session's role activation, so only an assigned role can become active, and the account menu shows the new role at once (`role_changed` event);
- hides results and history from the previous role, re-lists the Semantic Views the new role can open, and is audited, including refusals.

Neva never runs a query under a role the user did not activate or approve, and never uses a service credential in its place.

### Surface registry

`useNevaSurface` registers a page-owned context getter, safe capabilities, and starter actions. The registry reads the getter at turn time, so an editor selection or active tab does not depend on a stale render. Unmounting the page removes its registration and capabilities. New pages add their own registration; assistant core has no route-specific page switch.

Representative integrations cover SQL Workspace, Roles, Query History, Semantic Views, and Nova Studio. Each exposes the entity and view data relevant to that page and a small set of contextual starter actions. The SQL editor contributes its current selection, document ID, execution state, and result schema without copying result rows.

### Client capability bus

The model can request `invoke_client_capability` only for a capability advertised by the active surface. The backend checks a named action and bounded argument shape, then emits a `client_action` SSE frame with a correlation ID. The frontend checks the active surface again, validates arguments with the registered Zod schema, and calls the page-owned function. There is no DOM selector, arbitrary JavaScript, or coordinate interface.

Safe read and navigation actions execute without a consent card. Draft actions may prepare a reviewable state. Reversible, privileged, and destructive actions use the existing consent and authorization paths; the client bus rejects direct execution of those risk classes. A dispatched client action is **pending** until the page publishes a correlated success or failure event. Page refreshes propagate fetch errors so a failed reload cannot be acknowledged as a successful action.

SQL Workspace advertises the draft action `editor.propose_sql` (`sql` up to
20,000 characters, `mode` `replace` or `insert`) and sends the open
document's SQL as `editor.text` (bounded; withheld whole when it holds a
credential), so "this query" means the open file without a selection. When
the page offers the action, the planner selects the catalog lookup, the SQL
check and the action; the loop checks the drafted SQL before it proposes it.
The page shows the SQL as a diff with **Apply**, **Apply & Run** and
**Cancel**. Because a proposal runs nothing, the turn continues after the
`client_action` frame and Neva explains the change in the person's language.
Apply & Run of a proposal asks Neva to review the run only when it fails; a
repair still asks on every run.

`validate_sql` also asks the database to plan one `SELECT` or `WITH`
statement as the caller with `EXPLAIN` (15 s bound, nothing runs). That
checks that tables and columns exist and that the active role may read them.
A failed plan sends the reason back to the loop, which repairs the draft
before it is proposed. Statements with `@stage` references, writes and
several statements are not planned. After an approved write, `query_mutate`
looks at each created, altered or dropped table or view with `DESC` as the
caller and reports it done only if the role can see the result.

Every answer built from evidence carries its source in the `done` frame and
in its stored `answer` step: `semantic_view` or `glossary` (shown as
**Trusted**), `query`, `catalog` or `guide`. The panel shows it as a quiet
label under the answer, also after a reload.

Pages with their own context: SQL Workspace, Roles (list and detail),
Monitoring query history, Users, Database Explorer (the selected object as
the entity), Tasks and Business Ontology (open tab). Each offers only safe
actions: refresh, filters, selecting an item, opening a tab.

The provider client sends all system instructions as one leading system
message, in their original order. Some OpenAI-compatible gateways keep only
the first system message and silently drop the rest, which removed the page
context, the turn plan and the loop's corrections from the model's view.

Authenticated client action dispatch is recorded in `NOVA_SYSTEM.AUDIT_LOG` before the SSE action is sent. If that write fails, the action is not dispatched. Only capability and surface names are logged; arguments and page data are excluded.

A capability that intentionally navigates away from its surface declares `mayChangeSurface`; all other actions fail if the page changes while they run. The action's originating surface and correlation ID remain attached to its outcome event.

### Application events and SQL repair

The provider keeps a bounded recent event buffer. Pages publish events such as `query_started`, `query_failed`, `query_completed`, `editor_patch_applied`, `editor_patch_rejected`, `ui_action_completed`, and `ui_action_failed`. The next Neva turn includes relevant events from the current surface. Client action outcomes are also acknowledged immediately against the originating assistant thread using its correlation ID, so a dispatched action has a recorded outcome without another planner call. The backend uses events as outcome evidence, not as user instructions.

SQL Workspace records a generated execution ID, the active document, status, elapsed time, error, and bounded result schema. It omits result rows and credential-shaped SQL. A failed result offers **Explain** and **Fix with Neva**. Fix attaches the failed statement to the existing rewrite flow. The user reviews a diff and chooses **Apply**, **Apply & Run**, or **Cancel**. Apply publishes a patch event; Apply & Run starts a new query and publishes its result. The `inspect_query_error` tool reads the current failed execution. The `verify_query_repair` tool requires an applied patch and a successful rerun with the same correlation ID; applying a patch alone is not a verified fix.

The SQL rewrite records the editor content and attached line range used to build its diff. It refuses to apply if the document changed or the target statement moved. Duplicate statements without a unique recorded location are not attached for repair. Running SQL from a Neva code card publishes an artifact run and correlated query outcome, so a subsequent “fix it” can inspect that failure even when the page has no SQL execution widget.

The typed Ranger `grant_role_access` workflow reads effective access after submitting grants. It marks success only when every requested permission is visible in the readback. The audit outcome records the verification result. Ranger-to-StarRocks enforcement may still have propagation delay after this readback.

The `inspect_agent_configuration` tool reads the current Studio agent through the owner-scoped repository and returns bounded configuration facts, including a chart capability diagnostic. It does not read that agent's conversation or run the agent.

### Planning and observability

Simple supported UI requests can use a deterministic route to avoid a planner call. Other requests retain the provider-led agent loop and its bounded context manager. Packaged knowledge search boosts documented topics for a generic request on the current surface, while explicit user topics keep lexical priority. The turn trace records planner tier, active surface, context size, tool use, and outcome without logging application payloads or secrets.

## Integration Points

To make a new Nova page Neva-aware:

1. Register its stable surface ID, route, and a getter for the current entity, selection, view, and domain.
2. Register only typed actions the page actually supports. Use a strict Zod schema and the appropriate risk class.
3. Publish an outcome event after a meaningful execution or page action. Include the current document or entity ID and a correlation ID when one exists.
4. Add starter actions that describe useful tasks on that page.
5. Test context serialization, registration cleanup, action validation, events, and a representative user request.

```tsx
const { publishEvent } = useNevaSurface({
  id: "example.detail",
  route: "/example",
  context: () => ({ entity: { type: "example", id: item.id, name: item.name } }),
  capabilities: [
    defineNevaCapability({
      name: "surface.refresh",
      risk: "safe",
      argsSchema: z.object({}).strict(),
      execute: () => refreshItem(),
    }),
  ],
  suggestedActions: [{ label: "Explain this item", prompt: "Explain this item." }],
});

publishEvent({
  source: "surface",
  type: "entity_updated",
  status: "success",
  payload: { entityId: item.id },
});
```

## Configuration

The application context and event buffer are request-local or in-memory frontend state; no new database or credentials store is introduced. Existing assistant provider configuration, thread persistence in `NOVA_SYSTEM`, and consent policy continue to apply. The generic API bridge still uses its existing operation catalog and security filters.

The current client action vocabulary is intentionally small. Add a new action to the backend allowlist and a page registration only when its arguments and postcondition are explicit. The action must be covered by a behavior eval as well as frontend validation tests.
