"""Notebook outputs, uploads and the package allowlist."""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.core.exceptions import StorageError
from app.modules.notebooks.packages import PackageNotAllowed, validate_request
from app.modules.notebooks.runtime.kernel_calls import run_sql
from app.modules.notebooks.runtime.outputs import (
    OutputCollector,
    from_bundle,
    payload_of,
    strip_ansi,
    summary,
)
from app.modules.notebooks.runtime.provider import KernelEvent
from app.modules.notebooks.uploads import validate_upload


def test_bundles_prefer_tables_then_images_then_html_then_text():
    table = {"schema": [{"name": "a", "type": "INT"}], "rows": [[1]], "total_rows": 1}
    assert from_bundle({"application/vnd.nova.dataframe+json": json.dumps(table),
                        "text/html": "<table/>"})["type"] == "table"
    assert from_bundle({"image/png": "iVBORw0KGgo=", "text/plain": "<Figure>"})["type"] == "image"
    html = from_bundle({"text/html": "<b>x</b>", "text/plain": "x"})
    assert html == {"type": "html", "html": "<b>x</b>", "text": "x"}
    assert from_bundle({"text/plain": "\x1b[31mred\x1b[0m"}) == {"type": "text", "text": "red"}
    assert from_bundle({}) is None


def test_streams_merge_and_output_is_bounded_with_a_notice():
    collector = OutputCollector(max_bytes=200)
    collector.accept(KernelEvent(type="stdout", text="a"))
    collector.accept(KernelEvent(type="stdout", text="b"))
    assert collector.final() == [{"type": "stream", "name": "stdout", "text": "ab"}]
    collector.accept(KernelEvent(type="stderr", text="x" * 500))
    final = collector.final()
    assert collector.truncated and final[-1]["type"] == "truncated"


def test_kernel_errors_keep_a_bounded_clean_traceback():
    collector = OutputCollector(max_bytes=1_000_000)
    lines = [f"\x1b[32mline {i}\x1b[0m" for i in range(500)]
    collector.accept(KernelEvent(type="error", error={"name": "E", "value": "v",
                                                      "traceback": lines}))
    record = collector.final()[0]
    assert len(record["traceback"]) == 101 and "\x1b" not in "".join(record["traceback"])
    assert collector.error == {"name": "E", "message": "v"}


def test_payload_results_are_not_shown_as_output():
    collector = OutputCollector(max_bytes=10_000)
    bundle = {"application/vnd.nova.payload+json": json.dumps({"x": 1}), "text/plain": "<nova>"}
    assert collector.accept(KernelEvent(type="result", bundle=bundle)) is None
    assert payload_of(bundle) == {"x": 1} and collector.final() == []


def test_summary_describes_the_first_table():
    records = [{"type": "table", "table": {"schema": [{"name": "a"}], "rows": [[1], [2]],
                                           "total_rows": None, "origin": "nova"}}]
    assert summary(records)["table"] == {"columns": ["a"], "preview_rows": 2,
                                         "total_rows": None, "origin": "nova"}


def test_strip_ansi():
    assert strip_ansi("\x1b[1;31mError\x1b[0m: x") == "Error: x"


def test_sql_cells_reach_the_kernel_only_as_quoted_json():
    code = run_sql(source="SELECT '\"); import os; os.system(\"x' AS s", name=None,
                   engine="duckdb", preview_rows=10)
    literal = code.split("__nova__.run_sql(", 1)[1][:-1]
    import ast

    assert json.loads(ast.literal_eval(literal))["source"].startswith("SELECT '\");")


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("sales.csv", b"region,amount\nJakarta,100\n"),
        ("model.py", b"def f():\n    return 1\n"),
        ("table.parquet", b"PAR1" + b"\x00" * 10),
    ],
)
def test_supported_uploads_keep_their_name(name, data):
    assert validate_upload(name, data) == name


@pytest.mark.parametrize(
    ("name", "data", "status"),
    [
        ("../secret.csv", b"a", 400),
        ("/etc/passwd.csv", b"a", 400),
        ("folder/file.csv", b"a", 400),
        (".hidden.csv", b"a", 400),
        ("run.sh", b"echo", 400),
        ("fake.parquet", b"not parquet", 400),
        ("page.csv", b"<!DOCTYPE html><script>", 400),
        ("binary.txt", b"\x00\x01", 400),
        ("big.sql", b"x" * (5 * 1024 * 1024 + 1), 413),
    ],
)
def test_unsafe_uploads_are_refused(name, data, status):
    with pytest.raises(StorageError) as raised:
        validate_upload(name, data)
    assert raised.value.status_code == status


def test_only_allowlisted_packages_can_be_requested(monkeypatch):
    monkeypatch.setattr(settings, "NOTEBOOK_PACKAGE_ALLOWLIST", "scikit-learn,seaborn>=0.13")
    assert validate_request("Scikit_Learn", None) == {
        "name": "scikit-learn", "specifier": "", "import_name": "sklearn"}
    assert validate_request("seaborn", "0.13.2")["specifier"] == "==0.13.2,>=0.13"
    for bad_name, version in (("requests", None), ("seaborn", "1.0; rm -rf /"),
                              ("-r requirements.txt", None), ("seaborn", "../x")):
        with pytest.raises(PackageNotAllowed):
            validate_request(bad_name, version)
