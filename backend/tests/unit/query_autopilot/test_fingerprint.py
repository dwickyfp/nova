import pytest

from app.sql_frontend.fingerprint import fingerprint


@pytest.mark.parametrize(
    "a,b",
    [
        (
            "SELECT o.id FROM orders o WHERE o.total > 50",
            "select x.id from orders x where x.total > 100 /* note */",
        ),
        ("select id from orders where status='paid'", "select id from orders where status='open'"),
        (
            "select sum(total) as s from orders order by s",
            "select sum(total) as t from orders order by t",
        ),
        ("select id from orders where id in (1,2)", "select id from orders where id in (3,4)"),
    ],
)
def test_local_values(a, b):
    x, y = fingerprint(a), fingerprint(b)
    assert x.classified and y.classified
    assert x.family_id == y.family_id


@pytest.mark.parametrize(
    "a,b",
    [
        ("select a,b from t order by 1", "select a,b from t order by 2"),
        ("select a,b from t group by 1", "select a,b from t group by 2"),
        ("select a from t limit 10", "select a from t limit 100"),
        ("select a from t limit 1,10", "select a from t limit 10 offset 1"),
        ("select a from t order by a", "select a from t order by a desc"),
        ("select count(a) from t", "select count(distinct a) from t"),
        ("select a from t inner join u on t.id=u.id", "select a from t left join u on t.id=u.id"),
        ("select sum(a) over(partition by b) from t", "select sum(a) over(partition by c) from t"),
    ],
)
def test_structural_differences(a, b):
    assert fingerprint(a).family_id != fingerprint(b).family_id


@pytest.mark.parametrize(
    "sql",
    [
        "select rand() from t",
        "select now() from t",
        "select * from t limit 5",
        "select @password",
        "select * from files('path'='s3://bucket/key','aws.s3.secret_key'='secret')",
        "drop table t",
        "not sql",
    ],
)
def test_unsafe_replay(sql):
    result = fingerprint(sql)
    assert not result.replay_eligible
    assert "secret" not in (result.canonical or "")


def test_correlated_aliases():
    a = fingerprint(
        "select a.id from orders a where exists (select b.id from orders b where b.id=a.id)"
    )
    b = fingerprint(
        "select a.id from orders a where exists (select b.id from orders b where b.id=b.id)"
    )
    assert a.family_id != b.family_id


@pytest.mark.parametrize("clause", ["WHERE", "HAVING"])
def test_output_alias_does_not_erase_an_input_predicate_column(clause):
    a = fingerprint(f"SELECT SUM(x) AS a FROM t {clause} a=1")
    b = fingerprint(f"SELECT SUM(x) AS b FROM t {clause} b=2")
    assert a.classified and b.classified and a.version == b.version == 2
    assert a.family_id != b.family_id
    assert a.replay_eligible and b.replay_eligible


def test_relation_alias_does_not_rename_an_unqualified_input_column():
    a = fingerprint("SELECT a.x FROM t a WHERE a=1")
    b = fingerprint("SELECT b.x FROM t b WHERE b=1")
    assert a.classified and b.classified
    assert a.family_id != b.family_id


def test_output_and_relation_aliases_have_distinct_namespaces():
    a = fingerprint("SELECT a.id AS a FROM t a ORDER BY a")
    b = fingerprint("SELECT b.id AS b FROM t b ORDER BY b")
    assert a.classified and b.classified and a.replay_eligible and b.replay_eligible
    assert a.family_id == b.family_id


def test_grouping_and_window_names_do_not_acquire_output_alias_meaning():
    for template in (
        "SELECT x AS {name} FROM t GROUP BY {name}",
        "SELECT x AS {name},SUM(x) OVER(ORDER BY {name}) FROM t",
    ):
        a, b = (fingerprint(template.format(name=name)) for name in ("a", "b"))
        assert a.classified and b.classified and a.family_id != b.family_id
