"""nova_runtime inside a real IPython shell, as a notebook kernel runs it.

Run in the image: ``docker run --rm --entrypoint python nova-notebook-runtime:<tag>
-m pytest /opt/nova/tests``.
"""

from __future__ import annotations

import json
import math

import duckdb
import nova_runtime
import pandas as pd
import polars as pl
import pyarrow as pa
import pytest
from IPython.core.interactiveshell import InteractiveShell
from nova_runtime import refs, tables
from nova_runtime.jsonsafe import cell_value


def _data(payload) -> dict:
    return json.loads(payload._repr_mimebundle_()[nova_runtime.PAYLOAD_MIME])


@pytest.fixture(scope="module")
def shell(tmp_path_factory):
    ip = InteractiveShell.instance()
    workspace = tmp_path_factory.mktemp("workspace")
    ready = _data(nova_runtime.bootstrap(json.dumps({"workspace": str(workspace),
                                                     "preview_rows": 5})))
    assert ready["ready"] is True and ready["nova"] == {"attached": False, "catalogs": []}
    # The very first user query after bootstrap: no trusted statement may leave
    # the connection busy (DuckDB 2.0 keeps it busy until a result is read).
    result = ip.run_cell("db.sql('SELECT 1 AS ready').fetchall()[0][0]")
    assert result.error_in_exec is None and result.result == 1
    return ip


def run(shell, code: str):
    result = shell.run_cell(code)
    assert result.error_in_exec is None, result.error_in_exec
    return result.result


def test_db_and_the_default_connection_are_the_same(shell):
    run(shell, "db.execute('CREATE TABLE t AS SELECT range AS n FROM range(3)')")
    assert run(shell, "duckdb.sql('SELECT count(*) FROM t').fetchall()[0][0]") == 3
    assert run(shell, "db.sql('SELECT 1 AS ready').fetchall()[0][0]") == 1


def test_extensions_cannot_be_switched_back_on(shell):
    with pytest.raises(duckdb.Error):
        shell.user_ns["db"].execute("SET autoinstall_known_extensions = true")


def test_a_misspelt_table_does_not_search_other_catalogs(shell):
    # Searching every attached catalog for a suggestion takes minutes when
    # Postgres or Snowflake are attached; the cell must fail at once instead.
    assert run(shell, "db.sql(\"SELECT current_setting('catalog_error_max_schemas')\")"
                      ".fetchall()[0][0]") == 0
    run(shell, "db.execute(\"ATTACH ':memory:' AS other_catalog\")")
    run(shell, "db.execute('CREATE TABLE other_catalog.main.orders_elsewhere (a INT)')")
    out = _data(nova_runtime.run_sql(json.dumps({"source": "SELECT * FROM orders_elsewher"})))
    assert out["error"]["name"] == "CatalogException"
    assert "other_catalog" not in out["error"]["message"]
    run(shell, "db.execute('DETACH other_catalog')")


def test_sql_cell_names_a_lazy_result_for_python_and_later_sql(shell):
    out = _data(nova_runtime.run_sql(json.dumps(
        {"source": "SELECT n, n * 10 AS tens FROM t ORDER BY n", "name": "tens"})))
    assert out["kind"] == "table" and out["name"] == "tens"
    table = out["table"]
    assert [c["name"] for c in table["schema"]] == ["n", "tens"]
    assert table["rows"] == [[0, 0], [1, 10], [2, 20]] and table["total_rows"] == 3
    assert run(shell, "type(tens).__name__") == "DuckDBPyRelation"
    chained = _data(nova_runtime.run_sql(json.dumps(
        {"source": "SELECT sum(tens) AS s FROM {{tens}}", "name": "total"})))
    assert chained["table"]["rows"] == [[30]]


def test_preview_is_bounded_and_does_not_count(shell):
    out = _data(nova_runtime.run_sql(json.dumps(
        {"source": "SELECT * FROM range(1000000000)", "preview_rows": 5})))
    table = out["table"]
    assert len(table["rows"]) == 5 and table["truncated"] is True and table["total_rows"] is None


def test_references_to_frames_and_scalars(shell):
    run(shell, "people = pd.DataFrame({'name': ['a', 'b', 'c'], 'age': [30, 40, 50]})"
        if "pd" in shell.user_ns else
        "import pandas as pd\npeople = pd.DataFrame({'name': ['a','b','c'], 'age': [30,40,50]})")
    run(shell, "min_age = 35")
    out = _data(nova_runtime.run_sql(json.dumps(
        {"source": "SELECT name FROM {{people}} WHERE age > {{min_age}} ORDER BY name"})))
    assert out["table"]["rows"] == [["b"], ["c"]]


def test_references_inside_literals_and_comments_are_text(shell):
    out = _data(nova_runtime.run_sql(json.dumps(
        {"source": "SELECT '{{people}}' AS literal -- {{nothing}}\n"})))
    assert out["table"]["rows"] == [["{{people}}"]]


def test_unknown_reference_is_a_clear_error_not_an_eval(shell):
    out = _data(nova_runtime.run_sql(json.dumps({"source": "SELECT * FROM {{__import__}}"})))
    assert out["error"]["name"] == "ReferenceError"
    out = _data(nova_runtime.run_sql(json.dumps({"source": "SELECT * FROM {{nope}}"})))
    assert "not defined" in out["error"]["message"]


@pytest.mark.parametrize("text", ["{{ a b }}", "{{a.b}}", "{{1x}}", "{{x"])
def test_malformed_references_are_refused(text):
    with pytest.raises(refs.ReferenceError_):
        refs.find_references(f"SELECT * FROM {text}")


def test_writes_run_once_and_report_status(shell):
    out = _data(nova_runtime.run_sql(json.dumps(
        {"source": "CREATE TABLE once AS SELECT 1 AS x; INSERT INTO once VALUES (2)"})))
    assert out["kind"] == "status" and out["statement"] == "INSERT" and out["affected_rows"] == 1
    assert run(shell, "db.sql('SELECT count(*) FROM once').fetchall()[0][0]") == 2


def test_sql_errors_are_reported_with_their_message(shell):
    out = _data(nova_runtime.run_sql(json.dumps({"source": "SELECT * FROM missing_table"})))
    assert out["error"]["name"] == "CatalogException"
    assert "missing_table" in out["error"]["message"]


def test_nova_engine_without_nova_data_says_so(shell):
    out = _data(nova_runtime.run_sql(json.dumps({"source": "SELECT 1", "engine": "nova"})))
    assert "not available" in out["error"]["message"]


def test_displays_are_captured_in_order_and_returned_by_end(shell):
    nova_runtime.begin(json.dumps({"execution_id": "e1"}))
    run(shell, "from IPython.display import display, HTML\n"
               "display(HTML('<b>hi</b>'))\n"
               "display(pd.DataFrame({'x': [1, 2]}))\n"
               "import matplotlib\nmatplotlib.use('module://matplotlib_inline.backend_inline')\n"
               "import matplotlib.pyplot as plt\nplt.plot([1, 2]); plt.show()")
    result = _data(nova_runtime.end())
    kinds = [sorted(d["data"]) for d in result["displays"]]
    assert "text/html" in kinds[0]
    assert tables.TABLE_MIME in kinds[1]
    assert any("image/png" in k for k in kinds[2:])
    frame = json.loads(result["displays"][1]["data"][tables.TABLE_MIME])
    assert frame["rows"] == [[1], [2]] and frame["total_rows"] == 2


def test_displays_outside_an_execution_are_not_captured(shell):
    nova_runtime.end()
    run(shell, "display(HTML('<i>later</i>'))")
    assert _data(nova_runtime.end())["displays"] == []


def test_variables_describe_without_running_queries(shell):
    run(shell, "big = db.sql('SELECT * FROM range(10000000000)')\n"
               "frame = pl.DataFrame({'a': [1, 2, 3]})" if "pl" in shell.user_ns else
               "import polars as pl\nbig = db.sql('SELECT * FROM range(10000000000)')\n"
               "frame = pl.DataFrame({'a': [1, 2, 3]})")
    by_name = {v["name"]: v for v in nova_runtime.variables()}
    assert by_name["big"]["shape"] == "1 columns, lazy"
    assert by_name["frame"]["shape"] == "3 × 1"
    assert "db" not in by_name and "duckdb" not in by_name


def test_table_previews_cover_every_frame_kind():
    rel = duckdb.sql("SELECT * FROM range(10)")
    for value in (pd.DataFrame({"a": range(10)}), pl.DataFrame({"a": range(10)}),
                  pa.table({"a": list(range(10))}), rel, pl.DataFrame({"a": range(10)}).lazy()):
        out = tables.preview(value, limit=3)
        assert out is not None and len(out["rows"]) == 3 and out["truncated"] is True
    assert tables.preview(42, limit=3) is None


def test_cell_values_are_json_safe():
    assert cell_value(math.nan) == "NaN" and cell_value(-math.inf) == "-Infinity"
    assert cell_value(2**60) == str(2**60)
    assert cell_value(b"\x00\x01") == "<2 bytes>"
    import decimal
    assert cell_value(decimal.Decimal("2899000.00")) == 2899000
    assert cell_value(decimal.Decimal("12.50")) == 12.5
    assert cell_value(decimal.Decimal("12345678901234567.89")) == "12345678901234567.89"
    assert cell_value(pd.Timestamp("2026-11-10").to_pydatetime()) == "2026-11-10T00:00:00"
    json.dumps([cell_value(v) for v in (1, 1.5, None, True, "x" * 5000)])


def test_install_refuses_malformed_requests():
    out = _data(nova_runtime.install(json.dumps(
        {"packages": [{"name": "polars; rm -rf /", "specifier": ""}]})))
    assert out["ok"] is False
    out = _data(nova_runtime.install(json.dumps(
        {"packages": [{"name": "polars", "specifier": "==1; echo"}]})))
    assert out["ok"] is False


def test_nova_previews_keep_the_query_order():
    from nova_runtime.kernel import _nova_preview

    assert _nova_preview("SELECT a FROM t ORDER BY a", 5) == "SELECT a FROM t ORDER BY a\nLIMIT 6"
    assert _nova_preview("-- note\nWITH x AS (SELECT 1) SELECT * FROM x", 5).endswith("\nLIMIT 6")
    assert _nova_preview("SELECT a FROM t LIMIT 3", 5).startswith("SELECT * FROM (")
    assert _nova_preview("SHOW TABLES", 5) == "SHOW TABLES"
    assert _nova_preview("/* x */ describe t", 5) == "/* x */ describe t"


def test_a_nova_query_cell_that_writes_points_to_the_write_calls(shell, monkeypatch):
    from nova_runtime import kernel

    monkeypatch.setattr(kernel._STATE, "nova_attached", True)
    out = _data(nova_runtime.run_sql(json.dumps(
        {"source": "CREATE TABLE t (x INT)", "engine": "nova"})))
    assert out["error"]["name"] == "ValueError"
    assert "con.write" in out["error"]["message"]


# ── novaduck ──────────────────────────────────────────────────────────────


@pytest.fixture
def novaduck_con(shell, monkeypatch):
    import novaduck
    from nova_runtime import kernel

    monkeypatch.setattr(kernel._STATE, "catalogs", [
        {"name": "default_catalog", "alias": "nova", "type": "Internal", "attached": True},
        {"name": "pg_shop", "alias": "pg_shop", "type": "Jdbc", "attached": True},
    ])
    monkeypatch.setattr(kernel._STATE, "request_timeout", 5.0)
    return novaduck.connect()


def _answer_requests(result: dict, seen: list):
    """Stand in for the worker: answer the next request file once."""
    import os
    import threading
    import time

    from nova_runtime.novaduck import RPC_DIR

    def serve():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for name in os.listdir(RPC_DIR) if os.path.isdir(RPC_DIR) else []:
                if name.endswith(".json") and not name.endswith(".result.json"):
                    with open(os.path.join(RPC_DIR, name)) as handle:
                        request = json.load(handle)
                    data = os.path.join(RPC_DIR, request["data"])
                    if data.endswith(".parquet"):
                        seen.append((request, pa.parquet.read_table(data).to_pylist()))
                    else:
                        with open(data, "rb") as handle:
                            seen.append((request, handle.read()))
                    with open(os.path.join(RPC_DIR, name[:-5] + ".result.json"), "w") as out:
                        json.dump(result, out)
                    return
            time.sleep(0.01)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


def test_novaduck_is_the_notebook_connection(novaduck_con, shell):
    assert novaduck_con.duckdb is shell.user_ns["db"].duckdb
    assert novaduck_con.sql("SELECT 41 + 1").fetchall() == [(42,)]
    assert [c["alias"] for c in novaduck_con.catalogs()] == ["nova", "pg_shop"]


def test_novaduck_needs_no_import(shell):
    con = shell.user_ns["novaduck"].connect()
    assert con.duckdb is shell.user_ns["db"].duckdb


def test_timezone_timestamps_reach_python(shell):
    """DuckDB needs pytz for TIMESTAMP WITH TIME ZONE values."""
    [(value,)] = shell.user_ns["db"].sql(
        "SELECT TIMESTAMPTZ '2026-01-01 10:00:00+00'"
    ).fetchall()
    assert value.year == 2026 and value.tzinfo is not None


def test_merge_into_sends_the_rows_and_the_resolved_target(novaduck_con):
    import pyarrow.parquet  # noqa: F401

    seen: list = []
    thread = _answer_requests({"ok": True, "rows": 2}, seen)
    frame = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    result = novaduck_con.merge_into("pg_shop.public.orders", frame, on="id")
    thread.join()
    request, rows = seen[0]
    assert result == {"rows": 2}
    assert request["op"] == "merge" and request["on"] == ["id"]
    assert request["target"] == {"catalog": "pg_shop", "schema": "public", "table": "orders"}
    assert rows == [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]


def test_write_maps_nova_and_two_part_names_to_the_default_catalog(novaduck_con):
    import pyarrow.parquet  # noqa: F401

    seen: list = []
    thread = _answer_requests({"ok": True, "inserted": 3}, seen)
    novaduck_con.write("SELECT range AS n FROM range(3)", "sales.daily", mode="overwrite")
    thread.join()
    request, rows = seen[0]
    assert request["target"]["catalog"] == "default_catalog" and request["mode"] == "overwrite"
    assert [row["n"] for row in rows] == [0, 1, 2]


def test_a_refused_request_raises_with_novas_message(novaduck_con):
    import novaduck

    seen: list = []
    thread = _answer_requests({"ok": False, "error": "Role analyst may not INSERT"}, seen)
    with pytest.raises(novaduck.NovaError, match="may not INSERT"):
        novaduck_con.write(pl.DataFrame({"a": [1]}), "nova.s.t")
    thread.join()


def test_put_saves_in_the_format_the_name_says_and_copies_files(novaduck_con, tmp_path):
    seen: list = []
    thread = _answer_requests({"ok": True, "bytes": 1}, seen)
    novaduck_con.put(pa.table({"a": [1, 2]}), "@landing/daily/out.csv")
    thread.join()
    request, body = seen[0]
    assert request["op"] == "put" and request["data"].endswith(".csv")
    assert body.decode().splitlines() == ["a", "1", "2"]
    model = tmp_path / "model.pkl"
    model.write_bytes(b"\x80binary")
    thread = _answer_requests({"ok": True}, seen)
    novaduck_con.put(str(model), "@landing/models/model.pkl")
    thread.join()
    assert seen[1][1] == b"\x80binary"


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda con: con.write("SELECT 1", "nowhere.s.t"), "not a catalog"),
        (lambda con: con.write("SELECT 1", "t"), "catalog.schema.table"),
        (lambda con: con.write("SELECT 1", "nova.s.t", mode="upsert"), "append"),
        (lambda con: con.put("SELECT 1", "landing/x.parquet"), "@stage"),
        (lambda con: con.put("SELECT 1", "@landing/x.xlsx"), ".parquet"),
    ],
)
def test_bad_requests_are_refused_in_the_kernel(novaduck_con, call, message):
    with pytest.raises(Exception, match=message):
        call(novaduck_con)


def test_a_query_over_nova_catalogs_becomes_nova_sql(novaduck_con):
    from nova_runtime.kernel import nova_sql_candidate

    sql = ("SELECT r.region, n.total FROM \"nova\".sales.orders n "
           "JOIN pg_shop.public.regions r ON n.rid = r.id WHERE n.note <> 'nova.x' -- nova.y")
    assert nova_sql_candidate(sql) == (
        "SELECT r.region, n.total FROM default_catalog.sales.orders n "
        "JOIN pg_shop.public.regions r ON n.rid = r.id WHERE n.note <> 'nova.x' -- nova.y"
    )
    assert nova_sql_candidate("SELECT * FROM t") is None  # a local table
    assert nova_sql_candidate("SELECT * FROM read_csv('missing.csv')") is None
    assert nova_sql_candidate("SELECT * FROM other.s.t") is None  # not attached


def test_pygwalker_walk_is_shown_as_self_contained_html(shell, tmp_path, monkeypatch):
    import sys

    package = tmp_path / "pygwalker"
    package.mkdir()
    (package / "__init__.py").write_text(
        "def to_html(df, **kwargs):\n"
        "    return '<div id=gw>' + str(len(df)) + '</div><script>1</script>'\n"
        "def walk(df, **kwargs):\n"
        "    raise RuntimeError('the widget path must not run')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("pygwalker", None)
    nova_runtime.begin(json.dumps({"execution_id": "e-gw"}))
    run(shell, "import pandas as pd, pygwalker as pyg\npyg.walk(pd.DataFrame({'a': [1, 2, 3]}))")
    displays = _data(nova_runtime.end())["displays"]
    sys.modules.pop("pygwalker", None)
    assert displays and "<div id=gw>3</div>" in displays[-1]["data"]["text/html"]


def test_a_large_display_is_handed_over_as_a_file(shell):
    import os

    nova_runtime.begin(json.dumps({"execution_id": "e-big"}))
    run(shell, "from IPython.display import display, HTML\n"
               "display(HTML('<p>' + 'x' * 1_200_000 + '</p>'))")
    displays = _data(nova_runtime.end())["displays"]
    handed = displays[-1]
    assert "data" not in handed and handed["bytes"] > 1_200_000
    with open(handed["file"], encoding="utf-8") as body:
        assert json.load(body)["text/html"].startswith("<p>xxx")
    nova_runtime.begin(json.dumps({"execution_id": "e-next"}))
    assert not os.path.exists(handed["file"])
    nova_runtime.end()


def test_a_result_still_being_written_is_waited_for_and_kept(novaduck_con, monkeypatch):
    """The worker's upload finishes (permissions) after the content is readable.

    Removing the result file as soon as it is read made that upload fail, so the
    kernel keeps it, and it reads a half-written file again rather than failing.
    """
    import os
    import threading
    import time

    import pyarrow.parquet  # noqa: F401
    from nova_runtime import novaduck as module
    from nova_runtime.novaduck import RPC_DIR

    def serve():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for name in os.listdir(RPC_DIR) if os.path.isdir(RPC_DIR) else []:
                if name.endswith(".json") and not name.endswith(".result.json"):
                    target = os.path.join(RPC_DIR, name[:-5] + ".result.json")
                    with open(target, "w") as out:
                        out.write('{"ok": tr')  # half written
                        out.flush()
                        time.sleep(0.3)
                        out.write('ue, "inserted": 1}')
                    seen.append(target)
                    return
            time.sleep(0.01)

    seen: list = []
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert novaduck_con.write("SELECT 1 AS n", "sales.daily") == {"inserted": 1}
    thread.join()
    assert os.path.exists(seen[0])  # kept for the upload to finish
    monkeypatch.setattr(module, "_RESULT_STALE_SECONDS", 0.0)
    time.sleep(0.01)
    module._remove_stale_results()
    assert not os.path.exists(seen[0])
