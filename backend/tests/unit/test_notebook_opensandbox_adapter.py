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


def test_control_calls_keep_a_short_request_timeout(monkeypatch):
    monkeypatch.setattr(adapter.settings, "OPENSANDBOX_API_KEY", "test-only")
    assert adapter._connection_config().request_timeout.total_seconds() == 60
    assert adapter._connection_config(180).request_timeout.total_seconds() == 180


async def test_creation_waits_for_the_whole_start_budget(monkeypatch):
    import sys
    import types

    monkeypatch.setattr(adapter.settings, "OPENSANDBOX_API_KEY", "test-only")
    seen = {}

    class _Created(Exception):
        pass

    async def create(image, *, connection_config, **kwargs):
        seen["request"] = connection_config.request_timeout.total_seconds()
        raise _Created()

    fake = types.SimpleNamespace(Sandbox=types.SimpleNamespace(create=create))
    monkeypatch.setitem(sys.modules, "opensandbox", fake)
    monkeypatch.setitem(sys.modules, "code_interpreter", types.SimpleNamespace(
        CodeInterpreter=None, SupportedLanguage=None))
    spec = types.SimpleNamespace(
        image="runtime", egress=[], env={}, runtime_id="r", notebook_id="n", owner_hash="o",
        cpu="1", memory="1Gi", lifetime_seconds=600, ready_timeout_seconds=180,
    )
    with pytest.raises(ProviderError):
        await adapter.OpenSandboxProvider().create(spec)
    assert seen["request"] == 180


class _Response:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _Stream:
    def __init__(self, lines: list[str]) -> None:
        self._response = _Response(lines)

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _Codes:
    RUN_CODE_PATH = "/code"

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def _get_sse_client(self):
        lines = self._lines

        class _Client:
            def stream(self, method, url, json):  # noqa: A002, ARG002
                return _Stream(lines)

        return _Client()

    def _get_execd_url(self, path: str) -> str:
        return path


def _provider_streaming(monkeypatch, lines: list[str]) -> adapter.OpenSandboxProvider:
    provider = adapter.OpenSandboxProvider()
    live = adapter._Live(sandbox=None, interpreter=type("I", (), {"codes": _Codes(lines)})())

    async def connect(_sandbox_id: str):
        return live

    monkeypatch.setattr(provider, "_connect", connect)
    return provider


async def test_a_finished_run_passes_every_event_to_the_sink(monkeypatch):
    provider = _provider_streaming(monkeypatch, [
        'data: {"type": "init", "text": "exec-1"}',
        'data: {"type": "execution_complete"}',
        'data: {"type": "stdout", "text": "1\\n"}',
    ])
    seen: list[str] = []

    async def sink(event):
        seen.append(event.type)

    await provider.run(adapter.SandboxHandle("sb", "ctx"), "print(1)", sink)
    assert seen == ["init", "complete", "stdout"]


async def test_a_stream_that_ends_before_the_run_finishes_is_an_error(monkeypatch):
    # A lost connection does not prove whether the kernel ran the code.
    provider = _provider_streaming(monkeypatch, ['data: {"type": "init", "text": "exec-1"}'])

    async def sink(event):
        return None

    with pytest.raises(ProviderError, match="check its effects before running it again"):
        await provider.run(adapter.SandboxHandle("sb", "ctx"), "x = 1", sink)
