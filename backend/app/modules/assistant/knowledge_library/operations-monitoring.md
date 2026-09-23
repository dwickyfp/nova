# Tasks, monitoring, and performance operations

Keywords: tasks, task graph, scheduling, orchestration, monitoring, query history, active query, active queries, query cost, profile, explain, cluster, resource group, compaction, data loads, production health, audit trail.

Tasks at `/tasks` schedule and orchestrate SQL work. A Task Graph links task nodes and records graph and node attempts; the page shows task state and run history. Nove can explain the flow or draft supported task SQL, but a question about a particular run needs authorized runtime evidence.

Monitoring groups operational views: Query History, Active Queries, Audit Trail, Tasks, Query Cost, Data Loads, Cluster Monitor, and Production Health. Query History records what ran; Active Queries shows current work. EXPLAIN describes a proposed plan; Query Profile analyzes a completed query's actual execution. Query Cost and resource groups help manage capacity. Cluster Monitor reports FE/BE health and metrics. Compaction merges storage segments and affects table performance; it is an operational process rather than a user-facing data transformation.

Resource Groups route and bound workloads; queueing and classifiers can separate workloads. Monitoring and capacity decisions require live measurements rather than assumptions from reference docs. Audit Trail tracks Nova actions; it does not replace StarRocks authorization.

Implementation references: docs/08-task-manager.md, docs/12-resource-groups.md, docs/13-cluster-monitor.md, docs/17-query-profile.md, docs/26-compaction-manager.md; frontend/src/components/layout/data/sidebar-data.ts.
