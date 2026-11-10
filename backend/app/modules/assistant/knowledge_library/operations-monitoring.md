# Tasks, monitoring, and performance operations

Keywords: autopilot, query autopilot, tasks, task graph, scheduling, orchestration, monitoring, query history, active query, active queries, query cost, profile, explain, cluster, resource group, compaction, data loads, production health, audit trail.

Tasks (Data Management → Tasks) schedule and orchestrate SQL work. A Task Graph links task nodes and records graph and node attempts; the page shows task state and run history. Neva can explain the flow or draft supported task SQL, but a question about a particular run needs authorized runtime evidence.

Monitoring (Operations → Monitoring) groups AI Monitoring, Queries, Audit Trail, Data Loads, Cluster Monitor and Production Health. Queries has the tabs Running now (current work), History (what ran), Cost and Autopilot. Query Autopilot watches recurring queries, notices when they slow down or could run faster, tests a possible improvement safely and suggests it; nothing changes until an authorized person approves. Nothing needs to be turned on to read its findings. EXPLAIN describes a proposed plan; Query Profile analyzes a completed query's actual execution. Query Cost and resource groups help manage capacity. Cluster Monitor reports FE/BE health and metrics. Compaction merges storage segments and affects table performance; it is an operational process rather than a user-facing data transformation.

Resource Groups route and bound workloads; queueing and classifiers can separate workloads. Monitoring and capacity decisions require live measurements rather than assumptions from reference docs. Audit Trail tracks Nova actions; it does not replace StarRocks authorization.

