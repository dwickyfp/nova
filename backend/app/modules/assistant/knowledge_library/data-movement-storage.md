# Stages, storage, loading, export, and migration

Keywords: stage, storage connection, files, upload, download, loading, import, export, pipe, migration, backup, restore, sharing, data sharing.

A Stage is Nova's named, schema-bound file location. Users browse, upload, download, and remove files through Database Explorer, then refer to files in SQL as `@stage_name.path.file.csv`. Nova rewrites stage references to StarRocks FILES() with credentials injected server-side. The user-facing abstraction is the stage name; do not expose provider URLs or credentials. Storage Connections are administrator-defined in `nova.yaml` and viewed read-only in Admin Settings; they supply the underlying storage configuration for stages.

Data Loading moves files or streams into StarRocks tables. Nova supports stage-backed FILES() reads and loading workflows, plus StarRocks Stream Load, Broker Load, and Routine Load where configured. Data Export writes query results to a stage using Nova's stage syntax. A Pipe is a managed loading workflow; Tasks schedule or orchestrate SQL work. These are distinct: stages locate files, loading imports data, export writes data, and Tasks/Pipes automate execution.

Migration at `/migration` assesses and plans moving objects/data between compatible clusters. The current implementation describes assessment, dry-run, planning, and gated execution; do not promise lossless migration of every StarRocks object. Backup and Recovery use repository snapshots and restore/recycle-bin operations. Data Sharing can use views, shared stages, or external catalogs depending on the source and privileges. A storage volume is a StarRocks object used for table storage configuration; it is not the same as a Nova Stage.

Implementation references: docs/04-stage-manager.md, docs/09-pipe-manager.md, docs/14-storage-connections.md, docs/15-data-loading.md, docs/16-data-export.md, docs/21-backup-recovery.md, docs/25-storage-volumes.md, docs/27-data-sharing.md, docs/28-migration-connector.md. This reference explains product concepts, not the current status of a job or endpoint.
