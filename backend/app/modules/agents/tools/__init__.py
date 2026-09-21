"""Agent Studio tools — semantic querying and chart output (Phase 12).

These tools extend the Phase 10 assistant tool boundary. They obey the same
non-negotiables:

* delegate-first — they run on the requesting user's connection via
  ``query_service``, never on a service identity;
* in-process — never open a socket to StarRocks;
* credential-free — no tool holds or emits a secret;
* bounded — a row cap and a preview cap apply before context grows.

``semantic_query`` and ``semantic_search`` are read-only. ``data_to_chart`` is a
pure transform over data already fetched in the turn.
"""
