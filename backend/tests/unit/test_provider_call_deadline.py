"""A provider stream that stalls after its headers ends at the call's deadline."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.modules.assistant.provider import _lines_until


class _StalledStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices": []}\n\n'
        await asyncio.sleep(30)  # the provider never sends the rest
        yield b"data: [DONE]\n\n"


async def test_a_stalled_stream_times_out_at_the_deadline():
    response = httpx.Response(200, stream=_StalledStream())
    started = time.monotonic()
    lines = []
    with pytest.raises(httpx.ReadTimeout):
        async for line in _lines_until(response, time.monotonic() + 0.2):
            lines.append(line)
    assert time.monotonic() - started < 2
    assert lines and lines[0].startswith("data:")


async def test_a_finished_stream_ends_normally():
    response = httpx.Response(200, content=b"data: a\n\ndata: b\n\n")
    lines = [line async for line in _lines_until(response, time.monotonic() + 5)]
    assert [line for line in lines if line] == ["data: a", "data: b"]
