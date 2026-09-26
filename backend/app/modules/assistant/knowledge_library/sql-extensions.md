# Nova extensions and compatibility boundaries

Keywords: sql-extensions, Nova syntax, unsupported, extension, dialect, snowflake, stage, ml_predict, ml_forecast, ml_model, ai_complete, task, require password change.

Implemented interceptions in the shared QueryService:

| Family | Behavior | Guidance |
| --- | --- | --- |
| @stage.file.csv in a source | Resolve authorized stage, translate FILES, inject/redact credentials | stage-query |
| LIST @stage/ | Lower to FILES with file listing options | stage-query |
| COPY INTO db.table FROM @stage.file.csv | Lower to INSERT INTO table SELECT * FROM FILES | copy-into |
| CREATE ML_MODEL ... AS SELECT | Native Python ML training and model persistence | create-ml-model |
| ML_PREDICT('alias', features...) | Classification/regression inference when runtime is enabled | native-ml |
| SELECT * FROM ML_FORECAST(...) | Persisted forecast inference | native-ml |
| CREATE TASK ... AS statement | Nova task metadata and scheduler | create-task |
| ALTER USER ... REQUIRE PASSWORD CHANGE [OFF or NONE] | Nova login flag, administrative permission required | create-user |
| USE ROLE/SET ROLE name | Change the authenticated Nova session's active granted role | sql-operations |
| Grants/role SQL with Ranger enabled | Supported forms route to the access-control service | security-governance |

Ordinary native SQL passes through the shared guard and engine. Parser acceptance
alone is not execution evidence: it does not prove a function exists, an object
exists, privileges are sufficient, or the installed engine supports it.

The supported COPY form has one stage source, without Snowflake load options.
Use INSERT INTO table SELECT ... FROM @stage for projections. Stage export
also has translation support, but the ordinary SQL endpoint blocks it; a
dedicated authorized service must enable export explicitly.

Unsupported or distinct surfaces: CREATE STAGE, CREATE SEMANTIC VIEW, CREATE AGENT,
CREATE DASHBOARD, Snowflake warehouse DDL and Snowflake task lifecycle commands
are not Nova SQL. Use an available typed internal service tool or state that
execution is unavailable. Never invent REST routes as tools.

AI functions are AI_COMPLETE(text), AI_SENTIMENT(text), AI_CLASSIFY(text,
categories), AI_SUMMARIZE(text), AI_EXTRACT(text, schema), AI_TRANSLATE(text,
language), AI_FILTER(text, criteria). AI_SUMMARIZE has one argument. Provider
configuration and function installation are prerequisites; an error string
returned by a UDF is not a successful AI answer. Never include provider secrets.

ML has CLASSIFICATION, REGRESSION, FORECAST, ANOMALY_DETECTION and CLUSTERING.
CREATE ML_MODEL uses AS SELECT, not INPUT=(SELECT ...). Forecast requires TARGET,
TIMESTAMP and HORIZON. MODEL/MODEL_ID/VERSION inference forms belong to native-ml.
ML_EVALUATE SQL is not an implemented query interception; use model metrics and
the ML service. Never train or run inference merely to draft SQL.

Implementation references: app/modules/query/service.py,
app/modules/query/dialect/, app/modules/ml_engine/inference/,
app/modules/llm_functions/service.py, app/modules/access_control/statement_router.py.
