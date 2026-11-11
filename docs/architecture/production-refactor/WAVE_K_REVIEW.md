# Declared keys for absent source tables

These five tables were absent from the recorded live inspection. Their keys are
nevertheless explicit in current source DDL. `METADATA_OWNERSHIP.csv` now records
those declared keys without changing `source_rows=NOT_PRESENT`, claiming a live
constraint check, or creating any table.

| Table | Declared key, retained in the target mapping | Source evidence |
| --- | --- | --- |
| `CONFIG_DEEP_RESEARCH_RUNS` | `run_id` | `backend/app/modules/agents/deep_research.py`, `RUNS_DDL`; repository create/get initialize this schema on demand |
| `CONFIG_I18N_MESSAGES` | `language, message_id` | `backend/app/modules/assistant/messages.py`, `MESSAGES_DDL` |
| `ML_MODELS_PK` | `model_id` | `backend/app/common/nova_system.py`, `ML_MODELS_PK_DDL` |
| `ML_MODEL_ALIASES_PK` | `alias_name, owner_name, database_name` | `backend/app/common/nova_system.py`, `ML_ALIAS_PK_DDL` |
| `ML_MODEL_VERSIONS_PK` | `model_id, version` | `backend/app/common/nova_system.py`, `ML_MODEL_VERSIONS_PK_DDL` |

The three `_PK` tables are intermediate tables in `migrate_ml_metadata`, which
copies old data and renames the helper to its canonical name. Their absence is
consistent with completed bootstrap, but does not prove every previous copy or
rename succeeded. If a helper exists at final export, retain its rows and
reconcile it with the canonical and `_LEGACY` tables before importing. Do not
run bootstrap to destroy that evidence or automatically overwrite canonical rows.

The legacy Studio dashboard table has no source DDL in the inspected current
application. Its key remains unresolved until its actual export schema or a
verified historical definition is available. The seven duplicate-key tables
also require an import identity that retains duplicate rows. This review does
not close those mappings, timestamp provenance, per-role grants, or WP00.
