"""The technical-leak finder flags internals and leaves plain answers alone."""

import pytest

from app.modules.assistant.plain_language import find_technical_leaks


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Call POST /api/v1/assistant/threads to start", "route"),
        ("The /api/v1/monitoring/loads route is slow", "route"),
        ("The AI provider endpoint is not allowed", "endpoint"),
        ("Use the API to create it", "api"),
        ("AI provider returned HTTP 503", "http"),
        ("It is stored in NOVA_SYSTEM.CONFIG_TASK_RUNS", "system_table"),
        ("See CONFIG_ASSISTANT_THREADS", "system_table"),
        ("The bug is in service.py", "source_file"),
        ("Raised from app.modules.assistant.router", "module"),
        ('Traceback (most recent call last):\n  File "x.py", line 3', "stack_trace"),
        ("Open http://localhost:8000/docs", "address"),
        ("I ran query_execute to check", "tool_name"),
        ("Next I will call inspect_role_access.", "tool_name"),
        ("Open `/ai-providers` to check the key", "page_address"),
        ("Go to /monitoring/queries and pick Running now", "page_address"),
        ("Tasks at /tasks schedule SQL", "page_address"),
    ],
)
def test_flags_each_kind_of_internal(text, kind):
    assert kind in {leak.kind for leak in find_technical_leaks(text)}


@pytest.mark.parametrize(
    "text",
    [
        "Net revenue is the order value after discounts, minus returns.",
        "Open Monitoring, then Queries, and choose Running now.",
        "SELECT city, SUM(net_amount) FROM NOVA_RETAIL_FIELD.sales_orders GROUP BY city",
        "Pilih database dulu di pojok kanan atas, lalu jalankan lagi.",
        "Ask your administrator to check the AI service settings.",
        "The capital of Indonesia is Jakarta; rapid growth.",
        "COPY INTO sales FROM @stage1/tasks/orders.csv",
        "Read it and/or share it on 11/09/2026.",
        "Open AI & ML → Semantic Views.",
    ],
)
def test_leaves_plain_answers_alone(text):
    assert find_technical_leaks(text) == []


from app.modules.assistant import events  # noqa: E402
from app.modules.assistant.plain_language import (  # noqa: E402
    PlainTextStream,
    make_plain,
    plain_frames,
)


@pytest.mark.parametrize(
    "text",
    [
        "Set the endpoint and API key under AI Providers, then retry.",
        "The provider returned HTTP 503 from POST /api/v1/assistant/threads.",
        "I used query_execute and inspect_role_access to check it.",
        "It lives in NOVA_SYSTEM.CONFIG_TASK_RUNS (see app.modules.tasks.service).",
        "Open http://localhost:8000/docs for details.",
    ],
)
def test_make_plain_leaves_no_internals(text):
    assert find_technical_leaks(make_plain(text)) == []


def test_make_plain_keeps_meaning_in_plain_words():
    assert make_plain("I ran `inspect_role_access` first.") == "I ran an access check first."
    assert "access key" in make_plain("Add an API key")
    assert make_plain("Net revenue per city") == "Net revenue per city"


def test_a_term_split_across_chunks_is_still_caught():
    stream = PlainTextStream()
    chunks = ["The service said HT", "TP 5", "03 and the end", "point failed. Done"]
    out = "".join(stream.feed(chunk) for chunk in chunks)
    out += stream.flush()
    assert find_technical_leaks(out) == []
    assert out.startswith("The service said an error and the service address failed.")


def test_frames_are_plain_and_keep_their_order_and_position():
    apply = plain_frames()
    frames = [
        events.text_delta("Calling the ", content_index=0, content_id="c0"),
        events.text_delta("endpoint now please", content_index=0, content_id="c0"),
        events.thinking("act", "Running query_execute"),
        events.error("x", "AI provider returned HTTP 502"),
    ]
    out = [f for frame in frames for f in apply(frame)]
    out += apply.flush()
    text = "".join(out)
    assert find_technical_leaks(text.replace('"text_delta"', "")) == []
    # Held-back answer text is released before the next kind of frame.
    assert out[0].startswith("event: text_delta") and '"content_id":"c0"' in out[0]
    assert "event: thinking" in out[-2] and "a query" in out[-2]


def test_text_without_internals_streams_at_once():
    # Scripts without spaces, and ordinary words, are not held back.
    stream = PlainTextStream()
    assert stream.feed("売上は") == "売上は"
    assert stream.feed("増えました。") == "増えました。"
    assert stream.feed("Revenue grew 12% ") == "Revenue grew 12% "
    # Only an unfinished word that may begin an internal waits for the next chunk.
    assert stream.feed("check the end") == "check the "
    assert stream.feed(" of the month.") == "end of the month."


def test_code_a_person_copies_is_left_exactly_as_written():
    text = (
        'Use this:\n```json\n{"endpoint": "https://models.example.com"}\n```\n'
        'The endpoint is set.'
    )
    plain = make_plain(text)
    assert '{"endpoint": "https://models.example.com"}' in plain
    assert plain.endswith("The service address is set.")
    stream = PlainTextStream()
    chunks = ["Use this:\n``", '`json\n{"endpoint": 1}\n``', "`\nThe end", "point is set."]
    out = "".join(stream.feed(chunk) for chunk in chunks) + stream.flush()
    assert '{"endpoint": 1}' in out
    assert out.endswith("The service address is set.")


def test_a_page_address_becomes_the_menu_a_person_clicks():
    from app.modules.assistant.plain_language import make_plain

    text = make_plain("Open `/ai-providers`, then /monitoring/queries.")
    assert text == "Open Administrator → AI Providers, then Operations → Monitoring → Queries."
