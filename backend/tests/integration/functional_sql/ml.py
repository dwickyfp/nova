from __future__ import annotations

from uuid import uuid4

from .contracts import Case
from .runtime import NovaAPI


class MLFixtures:
    def __init__(self, api: NovaAPI, database: str, checkpoint=lambda: None, mode="balanced"):
        self.api = api
        self.database = database
        self.checkpoint = checkpoint
        self.models: list[dict] = []
        self.cases: list[Case] = []
        self.evidence: list[dict] = []
        self.last_sql = ""
        self.failed_result = None
        self.mode = mode

    def relay_cases(self) -> list[Case]:
        return [
            Case(
                f"ml.relay_training_{kind.lower()}",
                "ml",
                f"CREATE ML_MODEL relay_{kind.lower()} TYPE={kind} "
                + ("TARGET=y " if kind in {"CLASSIFICATION", "REGRESSION", "FORECAST"} else "")
                + ("TIMESTAMP=day HORIZON=2 " if kind == "FORECAST" else "")
                + "AS SELECT CAST(1 AS DOUBLE) AS x,7 AS y,DATE '2024-01-01' AS day",
                error_code=1235,
                error_contains="requires an API session",
                statement_rules=("createMlModelStatement",),
                contract_source="backend/app/sql_frontend/execution/adapters.py",
            )
            for kind in [
                "CLASSIFICATION",
                "REGRESSION",
                "FORECAST",
                "ANOMALY_DETECTION",
                "CLUSTERING",
            ]
        ]

    async def provision(self) -> tuple[list[Case], list[dict]]:
        outcomes = self.evidence
        cases = self.cases
        source = (
            f"(SELECT CAST(a.n*6+b.n AS DOUBLE) AS x FROM `{self.database}`.numbers a "
            f"CROSS JOIN `{self.database}`.numbers b) samples"
        )
        definitions = {
            "regression": "TYPE=REGRESSION TARGET=y FEATURES=(x) ALGORITHM=ridge "
            f"MODE={self.mode.upper()} AS SELECT x,CAST(7 AS DOUBLE) AS y FROM {source}",
            "classification": "TYPE=CLASSIFICATION TARGET=y FEATURES=(x) ALGORITHM=logistic "
            f"MODE={self.mode.upper()} AS SELECT x,IF(x<18,'low','high') AS y FROM {source}",
            "forecast": "TYPE=FORECAST TARGET=y TIMESTAMP=day HORIZON=2 FREQUENCY='D' "
            f"ALGORITHM=naive MODE={self.mode.upper()} AS SELECT date_add(DATE '2024-01-01', "
            f"INTERVAL CAST(x AS INT) DAY) AS day,CAST(7 AS DOUBLE) AS y FROM {source}",
            "anomaly_detection": "TYPE=ANOMALY_DETECTION FEATURES=(x) ALGORITHM=ecod "
            f"MODE={self.mode.upper()} AS SELECT x FROM {source}",
            "clustering": f"TYPE=CLUSTERING FEATURES=(x) MODE={self.mode.upper()} "
            f"AS SELECT x FROM {source}",
        }
        for kind, definition in definitions.items():
            name = "nova_sql_model_" + uuid4().hex[:12]
            fixture = {"name": name, "database_name": self.database, "kind": kind}
            self.models.append(fixture)
            self.checkpoint()
            sql = f"CREATE ML_MODEL {name} {definition}"
            self.last_sql = sql
            results = await self.api.request(
                "POST",
                "/api/v1/query/execute",
                json={"sql": sql, "database": self.database},
                timeout=330 if self.mode == "best" else 120,
            )
            if len(results) != 1 or not results[0]["success"]:
                import json

                self.failed_result = json.loads(self.api.target.safe(json.dumps(results)))
                messages = [item.get("error") for item in self.failed_result]
                raise RuntimeError(f"API training failed for {kind}: {messages}")
            row = results[0]["rows"][0]
            record = dict(zip(results[0]["columns"], row, strict=True))
            fixture.update(id=record["model_id"], version=record["version"])
            self.checkpoint()
            assert record["model_name"] == name and record["model_type"] == kind
            assert record["training_rows"] == 36 and record["status"] == "succeeded", record
            outcomes.append({"kind": kind, "sql": sql, "result": record})
            await self.api.request(
                "POST",
                "/api/v1/ml/aliases",
                json={
                    "alias_name": name,
                    "model_id": fixture["id"],
                    "version": fixture["version"],
                    "database_name": self.database,
                },
            )
            fixture["alias"] = name
            self.checkpoint()
            if kind == "regression":
                cases.append(
                    Case(
                        "ml.predict_regression",
                        "ml",
                        f"SELECT ML_PREDICT('{name}',CAST(n AS DOUBLE)) FROM numbers",
                        [[7.0]] * 6,
                    )
                )
                cases.append(
                    Case(
                        "ml.materialize_relay",
                        "ml",
                        f"SELECT * FROM ML_PREDICT_TABLE('{name}',"
                        "'SELECT CAST(n AS DOUBLE) AS x FROM numbers')",
                        error_code=1064,
                        error_contains="requires an API session",
                    )
                )
            elif kind == "classification":
                cases.append(
                    Case(
                        "ml.predict_classification",
                        "ml",
                        f"SELECT n,ML_PREDICT('{name}',CAST(n*7 AS DOUBLE)) "
                        "FROM numbers WHERE n IN (0,5)",
                        [[0, "low"], [5, "high"]],
                        "row_multiset",
                    )
                )
            elif kind == "forecast":
                cases.append(
                    Case(
                        "ml.forecast",
                        "ml",
                        f"SELECT * FROM ML_FORECAST(MODEL => '{name}',HORIZON => 2)",
                        {"horizon": 2, "dates": ["2024-02-06", "2024-02-07"], "value": 7.0},
                        "forecast",
                        statement_rules=("novaForecastStatement",),
                    )
                )
            else:
                if kind == "anomaly_detection":
                    cases.append(
                        Case(
                            "ml.predict_anomaly_detection",
                            "ml",
                            f"SELECT n,ML_PREDICT('{name}',CAST(IF(n=0,18,100000) AS DOUBLE)) "
                            "FROM numbers WHERE n IN (0,1)",
                            [[0, 0], [1, 1]],
                            "row_multiset",
                        )
                    )
                else:
                    cases.append(
                        Case(
                            "ml.predict_clustering",
                            "ml",
                            f"SELECT ML_PREDICT('{name}',CAST(n*7 AS DOUBLE)) "
                            "FROM numbers WHERE n IN (0,5)",
                            36,
                            "cluster_separation",
                        )
                    )
        cases.extend(
            [
                Case(
                    "ml.alias_missing",
                    "ml",
                    "SELECT ML_PREDICT('nova_sql_absent_alias',CAST(n AS DOUBLE)) "
                    "FROM numbers LIMIT 1",
                    error_code=1064,
                    error_contains="was not found",
                ),
                Case(
                    "ml.forecast_missing",
                    "ml",
                    "SELECT * FROM ML_FORECAST(MODEL => 'nova_sql_absent_alias',HORIZON => 2)",
                    error_code=1064,
                    error_contains="was not found",
                ),
            ]
        )
        return cases, outcomes

    async def close(self) -> None:
        aliases = (
            await self.api.request(
                "GET", "/api/v1/ml/aliases", params={"database_name": self.database}
            )
        )["aliases"]
        owned_names = {fixture["name"] for fixture in self.models}
        for alias in aliases:
            if alias["alias_name"] in owned_names:
                await self.api.request(
                    "DELETE",
                    "/api/v1/ml/aliases/" + alias["alias_name"],
                    params={"database_name": self.database},
                )
        available = (
            await self.api.request(
                "GET", "/api/v1/ml/models", params={"database_name": self.database}
            )
        )["models"]
        for fixture in reversed(self.models):
            matches = [item for item in available if item["model_name"] == fixture["name"]]
            for model in matches:
                result = await self.api.request(
                    "DELETE",
                    "/api/v1/ml/models/" + model["model_id"],
                    params={"database_name": self.database},
                )
                assert result["deleted"], "Model cleanup refused"
        remaining = (
            await self.api.request(
                "GET", "/api/v1/ml/models", params={"database_name": self.database}
            )
        )["models"]
        assert not {item["model_name"] for item in remaining} & {
            item["name"] for item in self.models
        }
