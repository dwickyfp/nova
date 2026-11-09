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
    ],
)
def test_leaves_plain_answers_alone(text):
    assert find_technical_leaks(text) == []
