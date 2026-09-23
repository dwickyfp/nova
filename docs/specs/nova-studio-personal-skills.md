# Nova Studio: Personal Skills and Internal Connectors

> Private reusable instructions, authored in chat or uploaded as SKILL.md, with an admin-managed connector catalog.

## Concept / Overview

Capabilities has two categories: **Skills** and **MCP Connectors**. The page does not display agents, raw tool names, or packaged skills. Packaged skills remain available to Nova internally and in the existing agent configuration registry.

The implementation uses the existing `NOVA_SYSTEM.CONFIG_AGENT_SKILLS` Primary Key table. All list, edit, and delete operations resolve the owner from the authenticated session. A client cannot choose another owner or publish a global skill. Skill mutations write audit events containing identifiers rather than document contents.

## Operations

| Action | Behavior |
| --- | --- |
| Create with chat | Opens a fresh Studio conversation with `/create-skill-with-chat ` in the composer. |
| Slash command | Typing `/` offers the authoring command. The user describes a repeated task and can refine the draft in follow-up messages. |
| Review and save | A complete fenced skill document in the assistant response opens an editable review dialog. Saving requires an explicit button click. |
| Upload a skill | Accepts UTF-8 `SKILL.md`, case-insensitive filename, up to 25 MB (26,214,400 bytes). Opens upload and verification steps. |
| Edit | Opens an owned skill's document; saves changes without changing its identifier. |
| Delete | Requires confirmation and removes only an owned skill. |
| Search | Filters skills or internal connectors by name and description. |

The server validates YAML frontmatter, a lowercase hyphenated name, a nonempty description, and Markdown instructions. It rejects aliases, anchors, oversized documents, credential-shaped values, packaged skill names, and existing names owned by the same user. Different users can use the same name.

Example file:

````markdown
---
name: weekly-review
description: Summarize a weekly report and identify changes that need follow-up.
---

Ask for the report and the comparison period.
Summarize the main changes using only the supplied evidence.
Separate unresolved questions from conclusions.
````

## Nova UI

```text
Capabilities       | Your skills                [ Search ] [ + Create ]
                   | -------------------------------------------------
[ Skills         ] |             Get started with skills
  MCP Connectors   |             [ Create a skill                    ]
                   |             [ Turn everyday tasks into          ]
                   |             [ workflows Nova can repeat.        ]
                   |
                   | Create menu: Create with chat / Upload a skill
                   |
                   | Populated: skill names, descriptions, edit/delete
```

Direction: a Nova management surface for its existing users; calm, readable, and action-oriented. ENERGY 1 / RHYTHM 2 / MOTION 1.

Design decisions:

- Nova's existing accent identifies the selected category. A secondary Create button matches the reference's compact toolbar; Save uses the existing primary button.
- Existing typography keeps Capabilities consistent with Studio; monospace is reserved for editing Markdown.
- A vertical Capabilities panel follows the CoWork reference. The right content area fills the available width, with its title and controls in one toolbar.
- A compact list lets users compare skill names and intended uses. The first-use state is one small Create a skill card; both it and Create open the same two-action menu.
- Document, conversation, upload, and search icons describe their adjacent actions using Nova's established icon set.
- Spacing separates navigation, management controls, and content; the empty state uses more space than populated rows.
- Dialog elevation identifies a temporary editing task. There are no decorative gradients, illustrations, or persistent animations.
- The existing themes supply all colors. The category navigation moves above content on mobile, controls wrap, and the Studio sidebar starts collapsed on narrow screens. Buttons, inputs, menus, and dialogs reuse the existing shadcn components.

## Implementation Notes

Upload opens a dedicated dialog with a dashed file picker and drop target, following the supplied upload reference. The footer separates Cancel from Verify skill. Nova supports one UTF-8 SKILL.md up to 25 MB; folders are not supported. Server verification checks the document parser and owner-scoped name availability without saving anything. The second step shows the name, description, and expandable source. Complete saves with the same server validation repeated; Back permits replacement, and errors remain retryable.

Large documents are stored as ordered UTF-8 text parts in NOVA_SYSTEM.CONFIG_SKILL_BODY_CHUNKS, created during backend schema initialization. Existing inline documents remain readable. A revision reference is published in the skill row only after every part is written; reads require a complete sequence and filter by owner, skill, and revision. Deleting a skill removes its parts. Superseded or interrupted revisions are retained until skill deletion. Parts fit the [StarRocks string limits](https://docs.starrocks.io/docs/sql-reference/data-types/string-type/STRING/). The 25 MB upload limit does not increase the agent context budget.

25 MB verification: browser boundary tests accept 26,214,400 bytes and reject one extra byte. Backend tests exercise the same boundary, Unicode chunk reconstruction, incomplete data detection, and failure before publication. The credential matcher omits a redundant unbounded prefix to avoid quadratic scanning of long strings; the existing credential-screening tests pass. These persistence checks use a fake database, not a live StarRocks cluster.

The modal retains Nova typography, semantic surface colors, standard Dialog/Button primitives, and existing radii. The dashed border identifies the drop target; the upload/file/check icons identify selection and verification states. Spacing separates input, review, and footer actions. Verification checks structure, not instruction accuracy or execution safety.

Upload verification gate: PASS. Nine browser tests cover upload, verification failure/retry, review, Back, Cancel, explicit Complete, existing management controls, and responsive themes. Twenty-three backend tests cover parsing, ownership, duplicate/reserved names, and verification without persistence. Production build and focused ESLint passed. Upload screenshots were inspected at 375 and 1440 px in light/dark. Tests use mocked API/database access; no live StarRocks or model call was made.

### API

| Route | Purpose |
| --- | --- |
| `GET /api/v1/agents/studio/skills` | List only caller-owned skills. |
| `POST /api/v1/agents/studio/skills` | Validate and save a `{document}` request. |
| `PUT /api/v1/agents/studio/skills/{id}` | Validate and update an owned document. |
| `DELETE /api/v1/agents/skills/{id}` | Delete an owned skill with audit logging. |
| `POST /api/v1/agents/studio/skill-author` | Return the platform-owned skill-author configuration without creating a user agent. |
| `GET /api/v1/agents/studio/capabilities` | Personal skill summaries and safe internal connector metadata. |

### Runtime

Agent composition discovers the owner's personal skills. Skill bodies are selected on demand through the existing skill router and bounded context machinery. A request-local `PersonalSkillLoader` also resolves explicit `load_skill` calls and checks the caller before returning a personal body. User procedures retain their subordinate trust level and do not grant new tools or permissions.

Authoring uses the existing assistant engine, provider configuration, thread history, cancellation, and context budget. An explicit user slash command puts that conversation in authoring mode; quoted commands or assistant messages do not activate it. Authoring uses an empty tool registry and a platform-owned authoring prompt, so writing a workflow does not execute it. Start a new chat to leave authoring mode.

### Internal connectors

The internal catalog uses the existing MCP table with the reserved owner `__nova__`. `ACCOUNTADMIN` manages it through the existing MCP management API. Ordinary users cannot create, update, delete, or discover servers. Studio shows only name, description, and catalog availability; it never shows endpoint, command, or arguments.

Previously user-owned connectors are not automatically promoted into the shared catalog. An administrator must deliberately register approved internal connectors. This avoids publishing private connector configurations during rollout. No new table migration is needed.

## Validation

- Production frontend build passed; existing Vite configuration and large-chunk warnings remain.
- Studio browser suite: 51 tests passed across capabilities, chat integration, shell routing, transcript behavior, and sidebar interactions.
- Backend capability/registry/authoring suite: 49 tests passed, including ownership, validation, audit, authoring bootstrap, and scripted engine trajectories.
- Engine scorecard: 32/32 scenarios and 96/96 checks passed.
- Browser coverage uses scripted API responses and real Chromium interactions. Backend tests use mocked persistence/provider seams; they do not establish live StarRocks or live model-provider connectivity.

Click-through evidence:

| Control | Observed result |
| --- | --- |
| Create menu | Opens both creation choices; chat selection invokes the handler. |
| Create with chat from Capabilities | Opens a fresh conversation and retains the slash-command prefill. |
| Slash suggestion | `/` offers the command; selecting it fills and focuses the composer. |
| Send during authoring | Scripted streamed draft exposes Review and save skill. |
| Review and save skill | Opens the complete document; no persistence request occurs before Save. |
| File upload | Reads a real browser File and opens the editor with its contents. |
| Save | Sends the reviewed document and refreshes skill queries. |
| Invalid save | Shows the error, retains the draft, and supports retry. |
| Skill row | Opens the edit dialog; Save sends the selected skill identifier. |
| Delete / Cancel | Cancellation sends no delete request; confirmation deletes the selected skill. |
| Search | Filters rows and displays a distinct no-match state. |
| MCP navigation | Loads internal entries and presents no Create action. |
| Retry | Recovers from a failed skill-list request. |
| Escape | Closes the Create menu; dialogs use the existing Radix Escape behavior. |
| Responsive/themes | Chromium checks at 375, 768, and 1440 px in light and dark; no horizontal overflow. |

## Antislop Delivery Gate

Embedded author and configuration cleanup: PASS. The skill author is a platform runtime configuration rather than a user agent record. The redundant Compiled agent contract panel is removed from configuration; editable instructions and server-side compilation remain intact. Nineteen Studio browser tests and two configuration browser tests pass, alongside 31 backend/trajectory tests and the 32-scenario engine scorecard (96 checks). TypeScript and focused lint pass; Studio chat retains two existing Fast Refresh lint warnings. Migration is tested with a fake database and has not been run against a live instance.

Scope: the new Capabilities, skill review, and authoring controls. Existing unrelated screens are not covered by this gate.

- PASS R-02: new UI copy contains no em dashes.
- PASS R-03: responsive browser checks and visual inspection of desktop/mobile screenshots show reflow without horizontal overflow.
- PASS R-17/R-18: no statistics or testimonials were introduced.
- PASS R-23: navigation follows the requested Skills/MCP categories; no new brand assets were invented.
- PASS R-24/R-26: control behavior is recorded in the click-through table above.
- PASS R-25: browser measurements of foreground/background, muted/background, primary text/primary, foreground/card, and muted/accent pass 4.5:1 in both themes.
- PASS R-27: loading, empty, no-match, and retryable error states are exercised in browser tests.
- PASS R-28: no FAQ was introduced.
- PASS R-32: controls use native buttons, inputs, and keyboard-enabled Radix primitives with visible focus treatments; Escape is exercised for the menu.
- PASS R-33: UI changes are source edits, with no source-rewriting helper scripts.
- PASS R-34/R-35: production build and recorded Chromium interactions passed; both theme screenshots were inspected.
- PASS R-36/R-38: the MCP surface lists internal connector metadata without an execution action; the execution limitation is documented below.
- PASS R-37: the user references and Nova's DESIGN.md informed the declared design direction.
- PASS R-01/R-04/R-06/R-07/R-08/R-09: colors, icons, typography, and labels have the written purposes above; no decorative backgrounds, arrows, or promotional badges were added.
- PASS R-10/R-12/R-13/R-14/R-19/R-22: flat list surfaces and an elevated editor serve hierarchy; no glow, glass, decorative motion, or invented illustrations.
- PASS Liveliness: explicit 1/2/1 dials, Create as the main action, structural whitespace, and the existing Nova accent and typography.
- PASS C-1/C-2/C-3/C-4/C-5: stated design reasons, working controls, content-driven composition, state/theme coverage, and explicit testing limits.
- PASS R-05/R-11/R-15/R-16/R-20/R-21/R-29/R-30/R-31: existing Nova tokens and radii, specific action labels, no marketing filler, and the requested reference workflow adapted to Nova's identity.

## Limitations

Skill creation is embedded in Studio through the reserved `nova-skill-author` runtime identity. It is never inserted into CONFIG_AGENTS or included in user agent lists/pickers, and its configuration cannot be edited through agent CRUD. Its private conversations use the existing thread APIs and bounded engine with an empty tool registry. Reloading a draft restores the embedded configuration directly. Startup migrates untouched legacy bootstrap agents only when their complete default configuration and creation audit match; their owner-scoped conversations move to the embedded identity before the obsolete configuration is removed and audited. Customized agents remain unchanged.

Skills contain Markdown instructions only: no archives, scripts, bundled resources, or arbitrary code execution. Chat authoring needs a configured model provider. MCP remains a catalog and discovery surface; remote MCP execution is outside this change. Skill-name duplicate checks are application-level, following the existing table schema; concurrent creates of the same name do not have a database uniqueness constraint.
