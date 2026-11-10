"""The OpenSandbox adapter: execd events keep MIME bundles, SDK errors stay safe to show."""

from __future__ import annotations

import pytest

from app.modules.notebooks.runtime import opensandbox_provider as adapter
from app.modules.notebooks.runtime.provider import ProviderError, SandboxGone


def test_a_result_keeps_every_mime_key_and_names_plain_text():
    event = adapter._kernel_event(
        {"type": "result", "timestamp": 1500,
         "results": {"text": "<Figure>", "image/png": "iVBOR", "text/html": "<b>x</b>"}}
    )
    assert event.type == "result" and event.timestamp == 1.5
    assert event.bundle == {"text/plain": "<Figure>", "image/png": "iVBOR", "text/html": "<b>x</b>"}


def test_streams_init_and_completion_are_typed():
    assert adapter._kernel_event({"type": "stdout", "text": "hi\n"}).text == "hi\n"
    assert adapter._kernel_event({"type": "stderr", "text": "warn"}).type == "stderr"
    assert adapter._kernel_event({"type": "init", "text": "exec-7"}).text == "exec-7"
    assert adapter._kernel_event({"type": "execution_complete"}).type == "complete"
    assert adapter._kernel_event({"type": "ping"}).type == "other"


def test_a_kernel_error_keeps_a_bounded_traceback():
    event = adapter._kernel_event(
        {"type": "error",
         "error": {"ename": "KeyError", "evalue": "'region'", "traceback": ["x"] * 500}}
    )
    assert event.error["name"] == "KeyError" and event.error["value"] == "'region'"
    assert len(event.error["traceback"]) == 200


def test_an_execd_failure_body_becomes_a_runtime_error():
    event = adapter._kernel_event({"code": "RUNTIME_ERROR", "message": "kernel died"})
    assert event.type == "error" and event.error["value"] == "kernel died"


def test_execd_busy_is_named_and_other_failures_stay_generic():
    assert "earlier cell" in adapter._execd_error('{"message": "session is busy"}')
    assert adapter._execd_error("not json") == "The notebook runtime could not run this cell"


class _Status(Exception):
    def __init__(self, status: int, text: str = "detail with a secret token") -> None:
        super().__init__(text)
        self.status_code = status


class ReadTimeout(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "kind", "message"),
    [
        (_Status(404), SandboxGone, "no longer running"),
        (Exception("SANDBOX_NOT_FOUND: gone"), SandboxGone, "no longer running"),
        (ReadTimeout("slow"), ProviderError, "did not answer in time"),
        (TimeoutError(), ProviderError, "did not answer in time"),
        (_Status(401), ProviderError, "could not authenticate"),
        (_Status(400), ProviderError, "refused the runtime settings"),
        (_Status(500), ProviderError, "service failed"),
    ],
)
def test_sdk_errors_are_classified_without_leaking_detail(exc, kind, message):
    error = adapter._sdk_error(exc)
    assert type(error) is kind and message in str(error)
    assert "secret" not in str(error)


def test_a_bare_address_becomes_a_single_host_rule():
    assert adapter._cidr("0.250.250.254") == "0.250.250.254/32"
    assert adapter._cidr("10.0.0.0/8") == "10.0.0.0/8"


def test_runtimes_are_not_configured_without_an_api_key(monkeypatch):
    monkeypatch.setattr(adapter.settings, "OPENSANDBOX_API_KEY", "")
    with pytest.raises(ProviderError, match="not configured"):
        adapter._connection_config()
