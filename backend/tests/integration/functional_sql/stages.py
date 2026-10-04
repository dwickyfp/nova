from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pyarrow as pa
import pyarrow.orc as orc
import pyarrow.parquet as parquet

from .contracts import Case
from .runtime import NovaAPI


class StageFixtures:
    def __init__(self, api: NovaAPI, database: str, connection: str, checkpoint=lambda: None):
        self.api = api
        self.database = database
        self.connection = connection
        self.stages: list[dict] = []
        self.checkpoint = checkpoint

    async def provision(self) -> list[Case]:
        table = pa.table({"id": [1, 2], "label": ["alpha", "beta"]})
        payloads = {
            "values.csv": b"id,label\n1,alpha\n2,beta\n",
            "values.json": b'{"id":1,"label":"alpha"}\n{"id":2,"label":"beta"}\n',
        }
        for extension, write in (("parquet", parquet.write_table), ("orc", orc.write_table)):
            buffer = BytesIO()
            write(table, buffer)
            payloads["values." + extension] = buffer.getvalue()
        for index in range(2):
            name = "nova_sql_files_" + uuid4().hex[:12]
            stage = await self.api.request(
                "POST",
                "/api/v1/stages",
                json={
                    "name": name,
                    "database_name": self.database,
                    "schema_name": "public",
                    "storage_connection": self.connection,
                    "base_prefix": f"functional-sql/{self.database}/{name}",
                },
            )
            self.stages.append(stage)
            self.checkpoint()
            selected = payloads if index == 0 else {
                name: payloads[name] for name in ("values.parquet", "values.csv")
            }
            for filename, content in selected.items():
                await self.api.request(
                    "POST",
                    f"/api/v1/stages/{stage['id']}/files",
                    files={"file": (filename, content, "application/octet-stream")},
                )
        left, right = [stage["name"] for stage in self.stages]
        cases = [
            Case(
                f"stage.read_{extension}",
                "stage",
                f"SELECT * FROM @{left}.values.{extension} ORDER BY 1",
                [[1, "alpha"], [2, "beta"]],
            )
            for extension in ("csv", "parquet", "orc")
        ]
        cases.append(
            Case(
                "stage.read_json_unsupported",
                "stage",
                f"SELECT * FROM @{left}.values.json",
                error_code=1064,
                error_contains="not supported format: json",
                contract_source=(
                    "https://github.com/StarRocks/starrocks/blob/"
                    "4a9848edf03f5c936dac664b2d52527f48e72eb0/"
                    "fe/fe-core/src/main/java/com/starrocks/catalog/TableFunctionTable.java"
                ),
            )
        )
        cases.extend(
            [
                Case(
                    "stage.list",
                    "stage",
                    f"LIST @{left}",
                    {name: len(content) for name, content in payloads.items()},
                    "file_listing",
                    statement_rules=("novaListStatement",),
                ),
                Case("stage.glob", "stage", f"SELECT COUNT(*) FROM @{left}/*.parquet", [[2]]),
                Case("stage.glob_csv", "stage", f"SELECT COUNT(*) FROM @{left}/*.csv", [[2]]),
                Case(
                    "stage.multiple",
                    "stage",
                    f"SELECT COUNT(*) FROM @{left}.values.parquet a "
                    f"JOIN @{right}.values.parquet b ON a.id=b.id",
                    [[2]],
                ),
                Case(
                    "stage.multiple_csv", "stage",
                    f"SELECT a.id,a.label,b.label FROM @{left}.values.csv a "
                    f"JOIN @{right}.values.csv b ON a.id=b.id ORDER BY a.id",
                    [[1, "alpha", "alpha"], [2, "beta", "beta"]],
                ),
                Case(
                    "stage.header_projection", "stage",
                    f"SELECT label AS chosen,id*2 AS doubled FROM @{left}.values.csv ORDER BY id",
                    [["alpha", 2], ["beta", 4]],
                ),
                Case(
                    "stage.copy_load",
                    "stage",
                    "SELECT * FROM stage_loaded ORDER BY id",
                    [[1, "alpha"], [2, "beta"]],
                    setup=(
                        "CREATE TABLE stage_loaded(id BIGINT,label VARCHAR(32)) "
                        "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                        "PROPERTIES('replication_num'='1')",
                        f"COPY INTO stage_loaded FROM @{left}.values.parquet",
                    ),
                    statement_rules=("novaCopyStatement",),
                ),
            ]
        )
        return cases

    async def close(self) -> None:
        for stage in reversed(self.stages):
            path = f"/api/v1/stages/{stage['id']}"
            response = await self.api.client.get(path)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            observed_stage = response.json()
            assert observed_stage["name"] == stage["name"], "Stage identity changed"
            assert observed_stage["database_name"] == self.database, "Stage scope changed"
            listing = await self.api.request("GET", path + "/files")
            for file in listing["files"]:
                await self.api.request("DELETE", path + "/files/" + file["name"])
            assert not (await self.api.request("GET", path + "/files"))["files"]
            await self.api.request("DELETE", path)
            response = await self.api.client.get(path)
            assert response.status_code == 404, "Stage metadata remains after cleanup"
