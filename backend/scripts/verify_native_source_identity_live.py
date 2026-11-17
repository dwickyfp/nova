"""Verify pinned-FE source identity using temporary roles and a synthetic database.

Use an existing QA principal, whose password is NOVA_QA_OWNER_PASSWORD. Its
existing roles, default role, Ranger attributes and policies are preserved.
Control credentials only provision and remove the uniquely named fixture;
all source metadata, DDL and data checks use the QA principal and one role.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import traceback
from pathlib import Path
from uuid import uuid4

import asyncmy

from app.core.config import settings
from app.core.database import db
from app.core.exceptions import StarRocksError
from app.integrations.ranger.client import ranger_client
from app.integrations.ranger.compiler import compile_access_policy, compile_bi_metadata_policy
from app.integrations.ranger.schemas import (
    RangerPolicy,
    RangerPolicyItem,
    RangerPolicyItemAccess,
    RangerPolicyResource,
    RangerRole,
    RangerRoleMember,
)
from app.modules.query.repository import QueryRepository
from app.sql_frontend.binding.models import TableName
from app.sql_frontend.binding.starrocks import StarRocksCatalogProvider
from app.sql_frontend.binding.types import SqlType
from app.sql_frontend.context import ExecutionContext
from app.sql_frontend.errors import SemanticError
from scripts._ranger_fixture import DATABASE_ACCESSES, TABLE_ACCESSES


async def run(
    principal: str,
    output: Path,
    *,
    dataset_output: Path | None = None,
    query_schema_output: Path | None = None,
    sql_dataset_output: Path | None = None,
    preview_output: Path | None = None,
    typed_table_output: Path | None = None,
    null_sql_output: Path | None = None,
    repair_source: bool = False,
    semantic_output: Path | None = None,
    parameter_dataset_output: Path | None = None,
) -> dict:
    if not principal.isascii() or not principal.replace("_", "").isalnum():
        raise ValueError("Expected an existing QA principal")
    password = os.environ["NOVA_QA_OWNER_PASSWORD"]
    suffix = uuid4().hex[:12]
    database, role, inactive = ("ndv_src_" + suffix, "ndv_read_" + suffix, "ndv_none_" + suffix)
    report = {
        "result": "RUNNING",
        "owned_database": database,
        "owned_roles": [role, inactive],
        "steps": [],
        "cleanup_errors": [],
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    policies = []
    repository = QueryRepository()

    def check(name: str, condition: bool) -> None:
        report["steps"].append({"check": name, "passed": condition})
        if not condition:
            raise AssertionError(name)

    def provider(active: str) -> StarRocksCatalogProvider:
        return StarRocksCatalogProvider(
            repository,
            ExecutionContext(
                principal,
                database=database,
                role=active,
            ),
            lambda: password,
        )

    async def execute(sql: str, active: str = role):
        result = await repository.execute_as_user(
            sql,
            principal,
            password,
            role=active,
            database=database,
        )
        check("governed fixture SQL succeeded", result.success)
        return result

    async def denied(name: str, active: str, source: str = "source_view") -> None:
        try:
            await provider(active).get_source_schema(TableName(source, database))
        except SemanticError:
            check(name, True)
        else:
            check(name, False)

    try:
        await db.init_system_pool()
    except Exception as error:
        report.update(result="FAIL", error_type=type(error).__name__, cleanup="NOT_CREATED")
        await db.close_system_pool()
        output.write_text(json.dumps(report, indent=2) + "\n")
        return report
    try:
        attributes = await ranger_client.get_user_attributes(principal)
        grants = await db.execute_system(f"SHOW GRANTS FOR '{principal}'")
        for name in (role, inactive):
            await db.execute_system(f"CREATE ROLE {name}")
            await ranger_client.put_role(
                RangerRole(
                    name=name,
                    users=[RangerRoleMember(name=principal)],
                )
            )
            await db.execute_system(f"GRANT {name} TO USER '{principal}'")
        await db.execute_system(f"CREATE DATABASE {database}")
        reflection = compile_bi_metadata_policy(roles=[role, inactive])
        for table_name, columns in {
            "tables_config": ["TABLE_ID", "TABLE_SCHEMA", "TABLE_NAME"],
            "tables": ["TABLE_SCHEMA", "TABLE_NAME", "TABLE_TYPE"],
            "columns": [
                "TABLE_SCHEMA",
                "TABLE_NAME",
                "COLUMN_NAME",
                "COLUMN_TYPE",
                "ORDINAL_POSITION",
                "IS_NULLABLE",
                "COLUMN_DEFAULT",
                "GENERATION_EXPRESSION",
            ],
        }.items():
            policy = reflection.model_copy(
                update={
                    "name": f"native-source-fixture/{suffix}/{table_name}",
                    "resources": {
                        **reflection.resources,
                        "table": RangerPolicyResource(values=[table_name]),
                        "column": RangerPolicyResource(values=columns),
                    },
                }
            )
            policies.append(policy.name)
            await ranger_client.put_policy(policy)
        for resource, accesses in ((None, TABLE_ACCESSES), ("database", DATABASE_ACCESSES)):
            policy = compile_access_policy(
                role=role,
                catalog="default_catalog",
                database=database,
                table="*",
                accesses=accesses,
                resource_type=resource,
            )
            policies.append(policy.name)
            await ranger_client.put_policy(policy)
        view_policy = RangerPolicy(
            service=settings.RANGER_SERVICE_NAME,
            name=f"native-source-fixture/{suffix}/views",
            resources={
                "catalog": RangerPolicyResource(values=["default_catalog"]),
                "database": RangerPolicyResource(values=[database]),
                "view": RangerPolicyResource(values=["*"]),
            },
            policyItems=[
                RangerPolicyItem(
                    roles=[role],
                    accesses=[
                        RangerPolicyItemAccess(type=action)
                        for action in ("select", "alter", "drop")
                    ],
                )
            ],
        )
        policies.append(view_policy.name)
        await ranger_client.put_policy(view_policy)
        # Policy propagation is observed with a read-only probe. Never replay DDL.
        async with asyncio.timeout(90):
            while True:
                try:
                    await repository.execute_as_user(
                        "SHOW TABLES",
                        principal,
                        password,
                        database=database,
                        role=role,
                    )
                    break
                except StarRocksError:
                    await asyncio.sleep(1)
        await execute(
            f"CREATE TABLE {database}.source_table "
            "(id BIGINT NOT NULL, amount DECIMAL(38,12) NULL) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            "PROPERTIES('replication_num'='1')"
        )
        await execute(
            f"CREATE VIEW {database}.source_view AS SELECT id, amount FROM {database}.source_table"
        )
        source = provider(role)
        # SHOW TABLES before creation proves database access, not that all
        # object/reflection policies have reached the FE. Probe both identities
        # before acceptance; this retries reads only, never the fixture DDL.
        attempts = 0
        async with asyncio.timeout(90):
            while True:
                attempts += 1
                try:
                    table = await source.get_source_schema(TableName("source_table", database))
                    view = await source.get_source_schema(TableName("source_view", database))
                    break
                except (SemanticError, StarRocksError):
                    await asyncio.sleep(1)
        report["reflection_readiness_attempts"] = attempts
        report["synthetic_view_schema"] = [
            {"name": column.name, "physical_type": column.data_type, "nullable": column.nullable}
            for column in view.columns
        ]
        check(
            "table and view have distinct physical IDs",
            bool(table.physical_id)
            and bool(view.physical_id)
            and table.physical_id != view.physical_id,
        )
        check(
            "empty source has authoritative decimal schema",
            not (await execute(f"SELECT * FROM {database}.source_view")).rows
            and view.columns[0].sql_type == SqlType("INTEGER", bits=64)
            and view.columns[1].sql_type == SqlType("DECIMAL", precision=38, scale=12)
            and view.columns[1].nullable is True,
        )
        check(
            "stable source schema",
            await source.get_source_schema(TableName("source_view", database)) == view,
        )
        if query_schema_output is not None:
            from scripts.verify_query_schema_live import run as verify_query_schema

            schema_report = await verify_query_schema(
                argparse.Namespace(
                    owner=principal,
                    role=role,
                    database=database,
                    base_url="http://127.0.0.1:5173",
                )
            )
            query_schema_output.write_text(json.dumps(schema_report, indent=2) + "\n")
            check("query schema through governed HTTP", schema_report["result"] == "PASS")
        if parameter_dataset_output is not None:
            from scripts.verify_native_parameter_dataset_live import run as verify_parameters

            # Rows exist only in this run's owned database.
            await execute(
                f"CREATE TABLE {database}.param_sales (id BIGINT NOT NULL, city VARCHAR(32), "
                "amount DECIMAL(18,2), sale_day DATE) DUPLICATE KEY(id) "
                "DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1')"
            )
            await execute(
                f"INSERT INTO {database}.param_sales VALUES "
                "(1, 'Jakarta', 10.50, '2024-02-28'), (2, 'Bandung', 20.00, '2024-02-29'), "
                "(3, 'Jakarta', 30.25, '2024-03-01')"
            )
            parameter_report = await verify_parameters(argparse.Namespace(
                owner=principal, outsider="bob", role=role, database=database,
                table=f"{database}.param_sales", base_url="http://127.0.0.1:5173",
            ))
            parameter_dataset_output.write_text(json.dumps(parameter_report, indent=2) + "\n")
            check("parameterized SQL dataset through governed HTTP",
                  parameter_report["result"] == "PASS")
        if preview_output is not None:
            from scripts.verify_native_datasets_live import run as verify_dataset

            # Only this run's owned fixture receives these rows. Existing QA
            # analytical tables and the caller's grants/attributes are unchanged.
            await execute(f"INSERT INTO {database}.source_table VALUES (2, 3.0), (3, 4.0)")
            exact = "99999999999999999999999999.123456789012"
            sql = (
                "SELECT id, CAST('9223372036854775807' AS BIGINT) AS exact_id, "
                f"CAST('{exact}' AS DECIMAL(38,12)) AS exact_amount, "
                "CAST('2024-02-29' AS DATE) AS day, "
                "CAST('2024-02-29 12:30:00' AS DATETIME) AS moment, "
                "CAST(NULL AS VARCHAR(20)) AS missing, TRUE AS enabled "
                "FROM source_view WHERE id >= 2 ORDER BY id"
            )
            preview_report = await verify_dataset(
                argparse.Namespace(
                    owner=principal,
                    outsider="bob",
                    role=role,
                    database=database,
                    object="source_view",
                    kind="view",
                    sql=sql,
                    columns=[
                        "id",
                        "exact_id",
                        "exact_amount",
                        "day",
                        "moment",
                        "missing",
                        "enabled",
                    ],
                    preview_rows=[
                        [
                            {"type": "integer", "value": str(index)},
                            {"type": "integer", "value": "9223372036854775807"},
                            {"type": "decimal", "value": exact},
                            {"type": "date", "value": "2024-02-29"},
                            {"type": "timestamp", "value": "2024-02-29T12:30:00+07:00"},
                            None,
                            {"type": "boolean", "value": True},
                        ]
                        for index in (2, 3)
                    ],
                    base_url="http://127.0.0.1:5173",
                )
            )
            preview_output.write_text(json.dumps(preview_report, indent=2) + "\n")
            check("exact governed dataset preview", preview_report["result"] == "PASS")
        if typed_table_output is not None or null_sql_output is not None:
            from scripts.verify_native_datasets_live import run as verify_dataset

            exact = "99999999999999999999999999.123456789012"
            await execute(
                f"CREATE TABLE {database}.preview_types (id BIGINT NOT NULL, flag BOOLEAN, "
                "day DATE, moment DATETIME, note VARCHAR(4100), amount DECIMAL(38,12), "
                "ratio FLOAT, magnitude DOUBLE) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                "PROPERTIES('replication_num'='1')"
            )
            await execute(
                f"INSERT INTO {database}.preview_types VALUES "
                "(1, NULL, NULL, NULL, '', NULL, -0.125, CAST('5e-324' AS DOUBLE)), "
                f"(2, TRUE, '2024-02-29', '2024-02-29 12:30:00', 'fixture', {exact}, "
                "1.5, 1.7976931348623157e308)"
            )
            expected = [
                [
                    {"type": "integer", "value": "1"},
                    None,
                    None,
                    None,
                    {"type": "string", "value": ""},
                    None,
                    {"type": "float", "value": -0.125},
                    {"type": "float", "value": 5e-324},
                ],
                [
                    {"type": "integer", "value": "2"},
                    {"type": "boolean", "value": True},
                    {"type": "date", "value": "2024-02-29"},
                    {"type": "timestamp", "value": "2024-02-29T12:30:00+07:00"},
                    {"type": "string", "value": "fixture"},
                    {"type": "decimal", "value": exact},
                    {"type": "float", "value": 1.5},
                    {"type": "float", "value": 1.7976931348623157e308},
                ],
            ]
            for case_output, selection in (
                (
                    typed_table_output,
                    {
                        "columns": ["id", "flag", "day", "moment", "note", "amount",
                                    "ratio", "magnitude"],
                        "preview_rows": expected,
                    },
                ),
                (
                    null_sql_output,
                    {
                        "sql": "SELECT id, amount FROM preview_types ORDER BY id",
                        "columns": ["id", "amount"],
                        "preview_rows": [[row[0], row[5]] for row in expected],
                    },
                ),
            ):
                if case_output is None:
                    continue
                typed_report = await verify_dataset(
                    argparse.Namespace(
                        owner=principal,
                        outsider="bob",
                        role=role,
                        database=database,
                        object="preview_types",
                        kind="table",
                        base_url="http://127.0.0.1:5173",
                        **selection,
                    )
                )
                case_output.write_text(json.dumps(typed_report, indent=2) + "\n")
                check(
                    "typed source dataset and null-first preview", typed_report["result"] == "PASS"
                )
        if semantic_output is not None:
            from scripts.verify_native_semantic_live import run as verify_semantic

            await execute(f"INSERT INTO {database}.source_table VALUES (5, 7.25)")
            semantic_report = await verify_semantic(argparse.Namespace(
                owner=principal, outsider="bob", role=role, database=database,
                base_url="http://127.0.0.1:5173",
                preview_rows=[[{"type": "integer", "value": "5"},
                               {"type": "decimal", "value": "7.250000000000"}]],
            ))
            semantic_output.write_text(json.dumps(semantic_report, indent=2) + "\n")
            check("semantic dataset uses published governed model",
                  semantic_report["result"] == "PASS")
        if sql_dataset_output is not None:
            from scripts.verify_native_datasets_live import run as verify_dataset

            sql_report = await verify_dataset(
                argparse.Namespace(
                    owner=principal,
                    outsider="bob",
                    role=role,
                    database=database,
                    object="source_view",
                    kind="view",
                    columns=["id", "doubled"],
                    sql="SELECT id, CAST(amount * 2 AS DECIMAL(38,12)) AS doubled FROM source_view",
                    history_marker="doubled",
                    base_url="http://127.0.0.1:5173",
                )
            )
            sql_dataset_output.write_text(json.dumps(sql_report, indent=2) + "\n")
            check("SQL dataset authoring on governed view", sql_report["result"] == "PASS")
        if dataset_output is not None:
            from scripts.verify_native_datasets_live import run as verify_dataset

            async def replace_view() -> None:
                await execute(f"DROP VIEW {database}.source_view")
                await execute(
                    f"CREATE VIEW {database}.source_view AS "
                    f"SELECT id, amount FROM {database}.source_table"
                )

            dataset_report = await verify_dataset(
                argparse.Namespace(
                    owner=principal,
                    outsider="bob",
                    role=role,
                    database=database,
                    object="source_view",
                    repair_source=replace_view if repair_source else None,
                    kind="view",
                    columns=["id", "amount"],
                    expected_types={"id": ["integer", "bigint"],
                                    "amount": ["decimal", "decimal"]},
                    base_url="http://127.0.0.1:5173",
                )
            )
            dataset_output.write_text(json.dumps(dataset_report, indent=2) + "\n")
            check("dataset API on governed empty view", dataset_report["result"] == "PASS")
        await denied("inactive role cannot discover view identity", inactive)
        await denied("missing view is refused", role, "absent_view")
        for ambiguous in ("NONE", "ALL"):
            async with (
                db.user_conn(principal, password) as connection,
                connection.cursor() as cursor,
            ):
                await cursor.execute("SET ROLE " + ambiguous)
                try:
                    await cursor.execute(
                        "SELECT TABLE_ID FROM information_schema.tables_config "
                        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='source_view'",
                        (database,),
                    )
                except asyncmy.errors.DatabaseError as error:
                    check(
                        "engine refuses " + ambiguous + " role metadata",
                        bool(error.args)
                        and isinstance(error.args[0], int)
                        and not 2000 <= error.args[0] < 3000
                        and "role" in str(error).lower(),
                    )
                else:
                    check("engine refuses " + ambiguous + " role metadata", False)
                if query_schema_output is not None:
                    try:
                        await cursor.execute(
                            f"EXPLAIN SCHEMA SELECT id FROM {database}.source_table"
                        )
                    except asyncmy.errors.DatabaseError as error:
                        check(
                            "query schema refuses " + ambiguous + " role",
                            bool(error.args)
                            and isinstance(error.args[0], int)
                            and not 2000 <= error.args[0] < 3000
                            and "role" in str(error).lower(),
                        )
                    else:
                        check("query schema refuses " + ambiguous + " role", False)
        await execute(f"ALTER TABLE {database}.source_table RENAME source_renamed")
        renamed = await source.get_source_schema(TableName("source_renamed", database))
        check("table rename retains physical identity", renamed.physical_id == table.physical_id)
        await denied("old table name requires repair", role, "source_table")
        await execute(f"ALTER TABLE {database}.source_renamed RENAME source_table")
        await execute(f"DROP VIEW {database}.source_view")
        await execute(
            f"CREATE VIEW {database}.source_view AS SELECT id, amount FROM {database}.source_table"
        )
        replaced = await source.get_source_schema(TableName("source_view", database))
        check(
            "drop recreate never reuses source identity", replaced.physical_id != view.physical_id
        )
        await db.execute_system(f"REVOKE {role} FROM USER '{principal}'")
        await denied("revoked role cannot read metadata", role)
        if query_schema_output is not None:
            try:
                await repository.execute_as_user(
                    "EXPLAIN SCHEMA SELECT * FROM source_view",
                    principal,
                    password,
                    role=role,
                    database=database,
                )
            except StarRocksError:
                check("revoked role cannot describe query output", True)
            else:
                check("revoked role cannot describe query output", False)
        report["result"] = "PASS"
    except Exception as error:
        report.update(result="FAIL", error_type=type(error).__name__)
        report["error_location"] = [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)[-3:]
        ]
    finally:

        async def clean(name, operation):
            try:
                await operation()
            except Exception as error:
                report["cleanup_errors"].append(
                    {"action": name, "error_type": type(error).__name__}
                )

        for policy_name in reversed(policies):

            async def remove_policy(name=policy_name):
                found = await ranger_client.get_policy(name)
                if found is not None:
                    await ranger_client.delete_policy(int(found["id"]))

            await clean("remove owned policy", remove_policy)
        await clean(
            "remove owned database",
            lambda: db.execute_system(f"DROP DATABASE IF EXISTS {database}"),
        )
        for role_name in (role, inactive):

            async def remove_role(name=role_name):
                if await ranger_client.get_role(name) is not None:
                    await ranger_client.delete_role(name)
                await db.execute_system(f"DROP ROLE IF EXISTS {name}")

            await clean("remove owned role", remove_role)
        try:
            check(
                "QA attributes unchanged",
                attributes == await ranger_client.get_user_attributes(principal),
            )
            check(
                "QA grants unchanged",
                grants == await db.execute_system(f"SHOW GRANTS FOR '{principal}'"),
            )
        except Exception as error:
            report["cleanup_errors"].append(
                {"action": "verify original QA identity", "error_type": type(error).__name__}
            )
        await db.close_system_pool()
        if report["cleanup_errors"]:
            report["result"] = "FAIL"
        report["engine_version"] = "4.1.4"
        report["limits"] = [
            "ALTER VIEW RENAME is absent from the pinned grammar; "
            "table rename and view drop/recreate are verified separately"
        ]
        report["ranger_service"] = settings.RANGER_SERVICE_NAME
        output.write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset-output", type=Path)
    parser.add_argument("--query-schema-output", type=Path)
    parser.add_argument("--sql-dataset-output", type=Path)
    parser.add_argument("--preview-output", type=Path)
    parser.add_argument("--typed-table-output", type=Path)
    parser.add_argument("--null-sql-output", type=Path)
    parser.add_argument("--repair-source", action="store_true")
    parser.add_argument("--parameter-dataset-output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(
        run(
            args.principal,
            args.output,
            dataset_output=args.dataset_output,
            query_schema_output=args.query_schema_output,
            sql_dataset_output=args.sql_dataset_output,
            preview_output=args.preview_output,
            typed_table_output=args.typed_table_output,
            null_sql_output=args.null_sql_output,
            repair_source=args.repair_source,
            parameter_dataset_output=args.parameter_dataset_output,
        )
    )
    print(json.dumps({"result": result["result"], "cleanup_errors": result["cleanup_errors"]}))
    raise SystemExit(0 if result["result"] == "PASS" else 1)
