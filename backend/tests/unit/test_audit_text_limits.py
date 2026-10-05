"""Audit text must fit the ``AUDIT_LOG`` string columns.

StarRocks ``STRING``/``TEXT`` columns hold at most 65533 bytes. A statement
longer than that (a large ``INSERT ... VALUES`` batch) used to make the audit
insert fail *after* the statement ran, so the client saw an error for a write
that had succeeded.
"""

from app.common import audit
from app.common.audit import AUDIT_TEXT_MAX_BYTES, fit_audit_text


def test_short_text_is_unchanged():
    assert fit_audit_text("SELECT 1") == "SELECT 1"
    assert fit_audit_text(None) is None


def test_long_text_is_truncated_within_the_byte_budget():
    text = "INSERT INTO t VALUES " + "(1,'é')," * 20_000
    fitted = fit_audit_text(text)
    assert len(fitted.encode("utf-8")) <= AUDIT_TEXT_MAX_BYTES
    assert fitted.startswith("INSERT INTO t VALUES (1,'é'),")
    removed = len(text.encode("utf-8")) - len(fitted.split(" /* [truncated")[0].encode("utf-8"))
    assert fitted.endswith(f"/* [truncated {removed} bytes] */")


def test_multibyte_characters_are_never_split():
    fitted = fit_audit_text("é" * 40_000, limit=1001)
    fitted.encode("utf-8")
    assert set(fitted.split(" /*")[0]) == {"é"}


async def test_audit_row_carries_fitted_and_redacted_text(monkeypatch):
    captured = {}

    async def execute_system(statement, parameters):
        captured["parameters"] = parameters

    monkeypatch.setattr(audit.db, "execute_system", execute_system)
    long_sql = (
        "SELECT * FROM FILES('aws.s3.secret_key'='SECRET_TESTVALUE') WHERE note = '"
        + "x" * 70_000
        + "'"
    )

    await audit.write_audit_log(
        event_type="query",
        user_name="u",
        action="execute",
        object_type="sql",
        object_name="db",
        status="SUCCESS",
        sql_text=long_sql,
        rewritten_sql=long_sql,
    )

    persisted = [value for value in captured["parameters"] if isinstance(value, str)]
    assert all(len(value.encode("utf-8")) <= AUDIT_TEXT_MAX_BYTES for value in persisted)
    assert not any("SECRET_TESTVALUE" in value for value in persisted)
