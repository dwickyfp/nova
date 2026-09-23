# City RBAC acceptance, 2026-09-23

## Plan and implementation

The acceptance target was one datasource, one role, two users, one shared
agent, and a city column that limits each user's visible rows. I provisioned
`rbac_city_demo.city_sales` with two Jakarta and two Bandung rows, made
`rbac_city_reader` the sole assigned role for `rbac_jakarta` and
`rbac_bandung`, and attached Ranger row filters keyed to each user. The
`rbac_city_sales` semantic model and `City RBAC Agent` are owned by
`nova_admin` and shared with the reader role. The agent loads model metadata
from its owner while executing every SQL query with the caller's StarRocks
identity and active role.

The live run exposed and fixed several integration faults: Ranger role and
policy response parsing, Ranger user synchronization, SELECT policy coverage
for columns, session role validation, access to a shared agent's bound model,
and backend encryption keys changing after container restarts. A semantic
question mentioning Bandung now keeps `city = 'Bandung'` in its validated plan.
When that authorized query has no rows, the assistant states that directly and
the Studio table shows an empty result without a misleading row-limit note.
Query audit entries now record the authenticated active role on success and
rejection.

## Acceptance matrix

| Surface | `rbac_jakarta` | `rbac_bandung` | Result |
| --- | --- | --- | --- |
| SQL Workspace | IDs 1, 3; Jakarta only | IDs 2, 4; Bandung only | Pass |
| Explicit other-city SQL filter | Zero rows | Zero rows | Pass |
| MySQL proxy on port 4406 | Jakarta only | Bandung only | Pass |
| Proxy handshake with forged `ACCOUNTADMIN` role | Denied | Denied | Pass |
| Proxy `USE ROLE ACCOUNTADMIN` | Denied; role unchanged | Denied; role unchanged | Pass |
| Nova Studio aggregate | Jakarta, `400.00` | Bandung, `600.00` | Pass |
| Jakarta asks agent for Bandung | Zero rows, clear empty-result answer | — | Pass |
| Live Chromium UI | Workspace and Studio scoped correctly; no page errors | Same | Pass |

The Workspace API test also sends a forged `ACCOUNTADMIN` role in the request
body. The server uses the authenticated session role and returns only the
caller's city. The Studio request does the same and remains scoped.

## Measurements

One final local run of `verify_city_rbac_demo.py` measured elapsed seconds:

| Operation | Seconds |
| --- | ---: |
| Jakarta Workspace, including allowed and denied query | 0.207 |
| Bandung Workspace, including allowed and denied query | 0.209 |
| Jakarta Studio aggregate | 9.398 |
| Bandung Studio aggregate | 11.318 |
| Jakarta Studio request for Bandung | 9.086 |

These are single-run local measurements, not p95 latency or a load benchmark.
Studio time includes the configured LLM provider and StarRocks round trips.

## Validation

- Focused backend tests: 50 passed, including shared-agent authorization,
  semantic filter preservation, Ranger compilation, role-aware auditing, and
  the empty-result trajectory.
- Agent behavior scorecard: 41/41 scenarios and 135/135 checks passed.
- Studio browser component tests: 26 passed.
- Frontend production build and targeted Ruff checks passed.
- Live Playwright script passed for both users and the Jakarta-to-Bandung
  denial; the browser reported no page errors.
- Backend logs from the final five-minute acceptance window contained zero
  `ERROR`, `Exception`, or `Traceback` lines.
- Eighteen recent query audit entries for the two test users recorded
  `active_role = rbac_city_reader` (10 Jakarta, 8 Bandung).

This demonstrates the specified two-user, two-city acceptance case on the
local StarRocks/Ranger/Nova stack. It does not claim every possible SQL shape,
provider response, or deployment configuration has been exhaustively tested.
