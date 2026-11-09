"""A sync error the redactor refuses is stored as a fixed message, not raised."""

from __future__ import annotations

from app.common.sql_guard import CredentialsRedactionError
from app.modules.catalog import sync


def test_an_error_holding_credentials_is_withheld_instead_of_raised(monkeypatch):
    def refuse(_text):
        raise CredentialsRedactionError("credential parameters remain populated")

    monkeypatch.setattr(sync, "redact_sql_credentials", refuse)
    message = sync.redact_error(RuntimeError("password='secret' Access denied"))
    assert "secret" not in message and "withheld" in message


def test_an_ordinary_error_is_kept_and_bounded():
    assert sync.redact_error("Unknown catalog pg_shop") == "Unknown catalog pg_shop"
    assert len(sync.redact_error("x" * 10_000)) <= sync._ERROR_LIMIT
