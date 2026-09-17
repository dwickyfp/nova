"""Response types shared by the controllers and the global exception handlers.

This module is a **leaf** on purpose. ``app/core/exceptions.py`` is imported by
``app/common/sql_guard.py``, which is imported by every query router; a response
class that the exception handler needs cannot live under ``app/modules/query``
without closing that cycle and breaking app import. Keeping it in ``app/common``
— importing nothing from ``app`` but the redactor it guards — lets both the
router and ``exceptions.py`` reach it in one direction only.
"""

from fastapi.responses import JSONResponse

from app.common.sql_guard import CredentialsRedactionError, redact_sql_credentials


class SanitizingJSONResponse(JSONResponse):
    """JSON response that strips storage credentials from the serialised payload.

    Last line of defence for AGENTS.md §2 (*Credentials NEVER in: API JSON
    responses*). Controllers already hand over redacted values — ``QueryResult``
    redacts ``executed_sql`` on construction — so this normally rewrites nothing.

    It exists because that guarantee is only as strong as the weakest caller:
    any endpoint added later that serialises an engine-bound statement (a plan,
    a rewrite preview, an error payload) would leak by default. Redacting the
    bytes here means a single forgotten call site degrades into a cosmetic
    ``***`` in one field instead of an exfiltrated storage key.

    Redaction is value-only and recursive, so the response keeps its shape and a
    client can still see which parameters were injected.

    A string the redactor refuses (``CredentialsRedactionError``) is replaced
    with a fixed placeholder rather than propagated: this runs inside
    ``render``, after the controller has already committed to a status code, so
    an exception here turns *any* response into a 500 — including the error
    handler's own output. Failing closed means never shipping the string, not
    crashing the response that carries it.
    """

    REDACTION_FAILED_PLACEHOLDER = "[redacted: unredactable credential value]"

    @staticmethod
    def _sanitize(value: object) -> object:
        if isinstance(value, str):
            try:
                return redact_sql_credentials(value)
            except CredentialsRedactionError:
                return SanitizingJSONResponse.REDACTION_FAILED_PLACEHOLDER
        if isinstance(value, dict):
            return {key: SanitizingJSONResponse._sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [SanitizingJSONResponse._sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [SanitizingJSONResponse._sanitize(item) for item in value]
        return value

    def render(self, content: object) -> bytes:
        return super().render(self._sanitize(content))
