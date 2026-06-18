# Nova — Sidebar Draft

> **Date:** June 18, 2026
> **Design:** Snowsight-style tree sidebar

---

## Sidebar Structure

```
┌──────────────────────────────────────┐
│  ⭐ Nova                             │
├──────────────────────────────────────┤
│  🔍 Search objects...               │
├──────────────────────────────────────┤
│                                      │
│  ▼ QUERY & EXPLORE                   │
│    📝 SQL Worksheet                  │
│    🕐 Query History                  │
│    📊 Query Profile                  │
│                                      │
│  ▼ DATA BROWSER                      │
│    ▶ 📦 default_catalog              │
│    ▶ 📦 datalake                     │
│      ▼ 📦 analytics                  │
│        ▼ 📁 bronze                   │
│          ▼ 📁 silver                 │
│            ▼ 📁 gold                 │
│              📋 customers            │
│              📋 orders               │
│              📋 products             │
│              👁️ v_monthly_revenue    │
│              📂 ext_stage            │
│              💻 calc_discount        │
│              📊 mv_top_products      │
│            ▶ 📁 staging              │
│            ▶ 📁 raw                  │
│    ▶ 📦 information_schema           │
│                                      │
│  ▼ EXTERNAL CATALOGS                 │
│    ▶ 🌐 hive_prod                    │
│    ▶ 🌐 iceberg_dev                  │
│    ▶ 🌐 paimon_lake                  │
│                                      │
│  ▼ AI & ML                           │
│    ✨ AI Functions                   │
│    🧠 ML Models                      │
│    📊 Dashboards                     │
│                                      │
│  ▼ ADMINISTRATION                    │
│    👥 Users & Roles                  │
│    ⚙️ Resource Groups               │
│    📡 Cluster Monitor                │
│    ✅ Tasks                          │
│    🔀 Pipes                          │
│    🛡️ Backup & Recovery              │
│    🔒 Data Governance                │
│    💾 Storage Connections             │
│    🗃️ Storage Volumes                │
│                                      │
│  ▼ SYSTEM                            │
│    ⬆️ Data Loading                   │
│    ⬇️ Data Export                    │
│    🗜️ Compaction                     │
│    🔗 Data Sharing                   │
│    🔍 Advanced Indexes               │
│    🔧 Variables                      │
│                                      │
├──────────────────────────────────────┤
│  🌙 dwicky          v0.1.0          │
└──────────────────────────────────────┘
```

---

## Item Icons

| Object | Icon | Color |
|--------|------|-------|
| Catalog | Database | default |
| Database | Database | blue |
| Schema | Folder | yellow |
| Table | Table2 | default |
| View | Eye | purple |
| Materialized View | BarChart3 | green |
| Stage | FolderOpen | orange |
| Function | Code | cyan |
| UDF | Zap | yellow |

---

## Behavior

| Action | Result |
|--------|--------|
| Click expand catalog | Load databases list |
| Click expand database | Load schemas list |
| Click expand schema | Load items (tables, views, stages, functions) |
| Click table | Main area: table detail (columns, data preview, DDL) |
| Click view | Main area: view detail + preview data |
| Click stage | Main area: file browser |
| Right-click item | Context menu: Rename, Drop, Copy Name, Properties |
| Search box | Filter objects across all catalogs (fuzzy) |

---

## Main Area — Table Detail

```
┌─────────────────────────────────────────────────┐
│ 📋 analytics.gold.customers                     │
│ Columns | Data Preview | DDL | Properties       │
├─────────────────────────────────────────────────┤
│                                                  │
│  Column        Type          Nullable  Default   │
│  ──────────────────────────────────────────────  │
│  customer_id   BIGINT        NO        -         │
│  name          VARCHAR(255)  NO        -         │
│  email         VARCHAR(255)  YES       NULL      │
│  created_at    DATETIME      NO        NOW()     │
│                                                  │
└─────────────────────────────────────────────────┘
```

---

## Design Notes

- **Catalog Explorer merged into Data Browser** — sidebar is the tree navigator
- **Snowsight-style** — hierarchical drill-down: Catalog → Database → Schema → Items
- **Flat menus** for non-data features: AI & ML, Administration, System
- **No S3/MinIO references** — storage abstracted behind Stages
