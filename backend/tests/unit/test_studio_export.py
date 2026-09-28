"""Exports: valid files, formula-safe cells, credentials redacted, rows re-run as caller."""

from __future__ import annotations

import csv
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import studio_router
from app.modules.agents.exporters import to_csv, to_pdf, to_pptx, to_xlsx

COLUMNS = ["city", "total_revenue"]
ROWS = [["Jakarta", 900], ["=HYPERLINK(\"x\")", 1], ["-5", -5]]


def test_csv_neutralises_formulas_but_keeps_negative_numbers():
    parsed = list(csv.reader(io.StringIO(to_csv(COLUMNS, ROWS).decode("utf-8-sig"))))
    assert parsed[0] == COLUMNS
    assert parsed[2][0].startswith("'=")
    assert parsed[3] == ["-5", "-5"]


def test_xlsx_is_a_valid_workbook_with_inline_strings():
    archive = zipfile.ZipFile(io.BytesIO(to_xlsx(COLUMNS, ROWS, title="Revenue/by city")))
    assert {"xl/workbook.xml", "xl/worksheets/sheet1.xml"} <= set(archive.namelist())
    sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "<v>900</v>" in sheet and "Jakarta" in sheet
    assert 'name="Revenue-by city"' in archive.read("xl/workbook.xml").decode()


def test_pdf_is_a_pdf():
    assert to_pdf(COLUMNS, ROWS, title="Revenue").startswith(b"%PDF")


def test_pptx_is_a_well_formed_deck_with_one_slide_per_twelve_rows():
    from xml.etree import ElementTree

    rows = [[f"City {index}", index] for index in range(30)]
    archive = zipfile.ZipFile(io.BytesIO(to_pptx(COLUMNS, rows, title="Revenue & <city>")))
    slides = sorted(name for name in archive.namelist()
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
    assert len(slides) == 3
    for name in archive.namelist():
        if name.endswith((".xml", ".rels")):
            ElementTree.fromstring(archive.read(name))  # every part parses
    first = archive.read(slides[0]).decode()
    assert "Revenue &amp; &lt;city&gt;" in first and "City 0" in first
    assert "City 12" not in first


def test_pptx_says_what_was_left_out():
    rows = [[f"City {index}", index] for index in range(70)]
    archive = zipfile.ZipFile(io.BytesIO(to_pptx(COLUMNS, rows, title="Revenue")))
    last = archive.read("ppt/slides/slide5.xml").decode()
    assert "Showing 60 of 70 rows" in last


async def test_export_re_runs_as_the_caller_and_redacts(monkeypatch):
    user = {"username": "alice", "encrypted_password": "enc", "session_id": "s",
            "roles": ["analyst"], "active_role": "analyst"}
    monkeypatch.setattr(studio_router, "_artifact_or_404", AsyncMock(return_value={
        "title": "Users", "sql_text": "SELECT 1", "database_name": "db", "schema_name": None,
    }))
    execute = AsyncMock(return_value=[SimpleNamespace(
        success=True, columns=["name", "api_key"], rows=[["a", "sk-abcdefghijklmnopqrstu"]],
    )])
    monkeypatch.setattr(studio_router.query_service, "execute_statements", execute)
    monkeypatch.setattr(studio_router, "write_audit_log", AsyncMock())
    response = await studio_router.export_artifact("art1", format="csv", user=user)
    assert execute.call_args.kwargs["username"] == "alice"
    assert b"sk-abcdef" not in response.body
    assert 'filename="Users.csv"' in response.headers["content-disposition"]
    with pytest.raises(HTTPException):
        await studio_router.export_artifact("art1", format="exe", user=user)
