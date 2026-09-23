# Nove product knowledge coverage

> Source map for the packaged, searchable feature explanations used by Nove.

## Purpose

Nove needs to answer what a Nova feature does without assuming that an object exists or running a data query. `search_knowledge` reads only curated files in `backend/app/modules/assistant/knowledge_library/`. A feature-help turn also receives the three highest ranked bounded excerpts in its prompt, so the answer does not depend on the model remembering to call a retrieval tool. Each excerpt has a source ID and content revision. Runtime claims still require an authorized tool.

Each reference has a curated `Keywords:` line. Exact topic aliases rank above incidental body mentions; multiword aliases rank above a generic single word. A unit matrix checks that all 25 sidebar labels resolve to the intended primary reference. The search reads only packaged reference files and screens credential-shaped content before returning it.

## Source map

| Packaged reference | Feature areas | Reviewed local sources |
| --- | --- | --- |
| `intelligence-search-entities` | Entities, AI Search, lexical/semantic/hybrid retrieval | `docs/28-intelligence-foundation.md`, Intelligence backend and UI |
| `intelligence-semantic-features` | Semantic View 2.0, Feature Views, Groups, online lookup, point-in-time training | `docs/28-intelligence-foundation.md`, Semantic/Feature services |
| `ml-ai-providers` | ML Models, AI Providers, embeddings, LLMs, Nove, Agent Studio | `docs/19-machine-learning.md`, `docs/28-native-ml-runtime.md`, assistant registry |
| `data-workspace-catalog` | Workspaces, Explorer, tables, views, functions, external catalogs, indexes | Modules 02, 03, 05, 06, 07, 10, 24 and current routes |
| `data-movement-storage` | Stages, connections, loads, export, pipes, migration, backup, volumes, sharing | Modules 04, 09, 14–16, 21, 25, 27, 28 Migration |
| `operations-monitoring` | Tasks, monitoring, profiles, resource groups, compaction | Modules 08, 12, 13, 17, 26 and sidebar |
| `security-governance` | Authentication, roles, ACCOUNTADMIN, Ranger, tags, lineage, settings | Modules 11, 18, 20, 22, 29 and access-control code |
| `dashboards-sharing` | Dashboards, charting, saved results, data sharing | Modules 02, 23, 27 and Workspaces UI |
| Existing skill and knowledge files | Nova SQL, stage syntax, procedures, query troubleshooting | `docs/sql_docs/`, Nove skill library |

The sources were checked against current routes and services before summarizing. Some module documents include target behavior. Packaged references describe only implemented or explicitly qualified behavior; they cannot establish live deployment health, data contents, grants, or provider configuration. On a feature change, update the relevant reference and its implementation citation, then add a retrieval and routing case to `test_nove_grounding.py` and a behavior trajectory to `tests/eval/` when tool selection or consent changes.

## Runtime boundary

Nove's registry now contains the same read-only, consent-gated `ai_search`, `semantic_view_query`, and `feature_lookup` tools used by Agent Studio. A question about a feature's purpose is routed to product help and receives reference knowledge only. A request to run the feature selects the corresponding tool and retains caller-scoped StarRocks/Ranger access and consent. The Semantic View tool exposes metrics, dimensions, filters, named filters, and optional version; Feature Lookup accepts an optional point-in-time timestamp.

StarRocks' official documentation confirms the underlying engine constraints used by these features: [vector indexing is a beta feature on shared-nothing clusters](https://docs.starrocks.io/docs/table_design/indexes/vector_index/), [ASOF JOIN is supported from v4.0](https://docs.starrocks.io/docs/sql-reference/sql-statements/table_bucket_part_index/SELECT/SELECT_JOIN/), and [a granted role must be activated to use its privileges](https://docs.starrocks.io/docs/sql-reference/sql-statements/account-management/SET_ROLE/). Nova-specific behavior remains defined by this repository's code and tests.
