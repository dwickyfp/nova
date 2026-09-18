"""Migration Connector — Phase 11 v1 (Assessment + Dry-run).

Connect to a source StarRocks cluster, enumerate its objects, and produce a
per-object dry-run verdict (``migratable`` / ``lossy`` / ``skipped``) without
executing any cutover. Execute is deliberately absent (gate #7); see
``docs/28-migration-connector.md``.
"""
