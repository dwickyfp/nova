from __future__ import annotations

from .contracts import Case, EffectCheck


def data_statement_cases() -> list[Case]:
    distribution = " DISTRIBUTED BY HASH(k) BUCKETS 1 PROPERTIES('replication_num'='1')"
    cases = []
    for model, columns, key, expected in (
        ("duplicate", "k INT NOT NULL,v INT", "DUPLICATE KEY(k)", [[1, 3], [1, 4]]),
        ("primary", "k INT NOT NULL,v INT", "PRIMARY KEY(k)", [[1, 4]]),
        ("unique", "k INT NOT NULL,v INT", "UNIQUE KEY(k)", [[1, 4]]),
        ("aggregate", "k INT NOT NULL,v INT SUM", "AGGREGATE KEY(k)", [[1, 7]]),
    ):
        table = "key_model_" + model
        cases.append(
            Case(
                "ddl.key_model_" + model,
                "ddl",
                f"INSERT INTO {table} VALUES (1,3),(1,4)",
                [],
                setup=(f"CREATE TABLE {table}({columns}) {key}" + distribution,),
                statement_rules=("createTableStatement", "insertStatement"),
                effects=(EffectCheck(f"SELECT k,v FROM {table} ORDER BY k,v", expected),),
            )
        )
    cases.extend(
        [
            Case(
                "ddl.generated_column",
                "ddl",
                "INSERT INTO generated_values(k) VALUES (2),(5)",
                [],
                setup=(
                    "CREATE TABLE generated_values(k INT NOT NULL,doubled BIGINT AS k*2) "
                    "DUPLICATE KEY(k)" + distribution,
                ),
                statement_rules=("createTableStatement", "insertStatement"),
                effects=(
                    EffectCheck(
                        "SELECT k,doubled FROM generated_values ORDER BY k", [[2, 4], [5, 10]]
                    ),
                ),
            ),
            Case(
                "ddl.range_partition",
                "ddl",
                "INSERT INTO partitioned_values VALUES (2,3),(12,4)",
                [],
                setup=(
                    "CREATE TABLE partitioned_values(k INT NOT NULL,v INT) DUPLICATE KEY(k) "
                    "PARTITION BY RANGE(k) (PARTITION low VALUES [('0'),('10')), "
                    "PARTITION high VALUES [('10'),('20')))" + distribution,
                ),
                statement_rules=("createTableStatement", "insertStatement"),
                effects=(
                    EffectCheck("SELECT k,v FROM partitioned_values ORDER BY k", [[2, 3], [12, 4]]),
                ),
            ),
            Case(
                "dml.insert_select",
                "dml",
                "INSERT INTO inserted_values SELECT n,n*2 FROM numbers",
                [],
                setup=(
                    "CREATE TABLE inserted_values(k INT NOT NULL,v INT) DUPLICATE KEY(k)"
                    + distribution,
                ),
                statement_rules=("insertStatement",),
                effects=(
                    EffectCheck(
                        "SELECT k,v FROM inserted_values ORDER BY k", [[n, n * 2] for n in range(6)]
                    ),
                ),
            ),
            Case(
                "dml.update_confirmation_required",
                "dml",
                "UPDATE update_values SET v=9 WHERE k=1",
                error_code=1064,
                setup=(
                    "CREATE TABLE update_values(k INT NOT NULL,v INT) PRIMARY KEY(k)"
                    + distribution,
                    "INSERT INTO update_values VALUES (1,3)",
                ),
                statement_rules=("updateStatement",),
                effects=(EffectCheck("SELECT k,v FROM update_values", [[1, 3]]),),
            ),
            Case(
                "dml.delete_confirmation_required",
                "dml",
                "DELETE FROM delete_values WHERE k=1",
                error_code=1064,
                setup=(
                    "CREATE TABLE delete_values(k INT NOT NULL,v INT) PRIMARY KEY(k)"
                    + distribution,
                    "INSERT INTO delete_values VALUES (1,3)",
                ),
                statement_rules=("deleteStatement",),
                effects=(EffectCheck("SELECT k,v FROM delete_values", [[1, 3]]),),
            ),
            Case(
                "query.union_all",
                "query",
                "SELECT 1 UNION ALL SELECT 1",
                [[1], [1]],
            ),
            Case(
                "query.exists_correlated",
                "query",
                "SELECT a.n FROM numbers a WHERE EXISTS "
                "(SELECT 1 FROM numbers b WHERE a.n=b.n AND b.n<2) ORDER BY a.n",
                [[0], [1]],
            ),
            Case(
                "query.full_join",
                "query",
                "SELECT a.n,b.n FROM (SELECT n FROM numbers WHERE n<2) a FULL JOIN "
                "(SELECT n FROM numbers WHERE n BETWEEN 1 AND 2) b ON a.n=b.n "
                "ORDER BY COALESCE(a.n,b.n)",
                [[0, None], [1, 1], [None, 2]],
            ),
            Case(
                "query.quoted_identifier",
                "query",
                "SELECT `select` FROM (SELECT n AS `select` FROM numbers) `order` "
                "ORDER BY `select` LIMIT 2",
                [[0], [1]],
            ),
            Case(
                "query.rollup",
                "window",
                "SELECT n%2 AS k,COUNT(*),GROUPING(n%2) FROM numbers "
                "GROUP BY ROLLUP(n%2) ORDER BY k NULLS FIRST",
                [[None, 6, 1], [0, 3, 0], [1, 3, 0]],
            ),
            Case(
                "query.cube",
                "window",
                "SELECT n%2 AS k,COUNT(*),GROUPING(n%2) FROM numbers "
                "GROUP BY CUBE(n%2) ORDER BY k NULLS FIRST",
                [[None, 6, 1], [0, 3, 0], [1, 3, 0]],
            ),
        ]
    )
    return cases
