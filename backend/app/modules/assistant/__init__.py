"""Assistant module — bounded, delegate-first SQL assistant (Phase 10).

Scope and contracts are frozen in ``docs/specs/nova-61-agentic-assistant-design.md``
(NOVA-61 T-A0). This package implements Stage B: the module skeleton, the
provider wiring (E4a), the in-memory thread state (E5a), and the bounded
plan → tool → reflect loop with the SSE event contract.

Design rules enforced here, not merely documented:

* **Only redacted SQL may leave the process.** The tool layer receives a
  ``QueryResult`` whose ``executed_sql`` is already redacted at construction
  (``app/modules/query/repository.py``). The assistant never holds an engine
  statement that carries injected ``FILES()`` credentials.
* **No credential ever enters a thread, event, or provider request.**
  ``encrypted_password`` stays on the request-side user dict; provider API keys
  live only for the duration of one outbound call.
* **The model loop is bounded.** A hard iteration cap and a wall-clock budget
  terminate a runaway turn rather than letting it hold a worker indefinitely.
"""
