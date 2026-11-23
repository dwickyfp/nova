"""The parser warm-up trains every thread's DFA within a bounded budget."""

from __future__ import annotations

import inspect
import threading

import pytest

from app.sql_frontend import parser as sql_parser
from app.sql_frontend import warmup


@pytest.mark.parametrize("sql", warmup.WARMUP_CORPUS)
def test_every_warmup_statement_parses(sql):
    _, _, errors = sql_parser.parse_tree(sql)
    assert errors == []


def test_warmup_reaches_the_serving_thread_and_every_worker(monkeypatch):
    monkeypatch.setattr(warmup.settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 8192)
    seen: list[tuple[int, bool]] = []
    real = sql_parser.parse_tree

    def recording(sql):
        seen.append((threading.get_ident(), sql_parser._worker_thread.active))
        return real(sql)

    monkeypatch.setattr(sql_parser, "parse_tree", recording)
    report = warmup.warm_parser()
    corpus = len(warmup.WARMUP_CORPUS)
    threads = warmup.settings.SQL_PARSE_THREADS
    assert report.complete
    assert report.worker_threads == threads
    assert report.statements == corpus * (threads + 1)
    # One pass on this (non-worker) thread, one on each distinct worker thread.
    worker_ids = {ident for ident, worker in seen if worker}
    assert len(worker_ids) == threads
    assert sum(1 for ident, worker in seen if not worker) == corpus
    assert threading.get_ident() not in worker_ids


def test_warmup_without_offload_warms_only_the_serving_thread(monkeypatch):
    monkeypatch.setattr(warmup.settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 0)
    report = warmup.warm_parser()
    assert report.complete and report.worker_threads == 0
    assert report.statements == len(warmup.WARMUP_CORPUS)


def test_warmup_stops_at_its_budget_and_never_raises(monkeypatch):
    monkeypatch.setattr(warmup.settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 8192)
    report = warmup.warm_parser(budget_seconds=0.0)
    assert not report.complete
    assert report.statements < len(warmup.WARMUP_CORPUS) * (
        warmup.settings.SQL_PARSE_THREADS + 1
    )


def test_a_failing_statement_does_not_fail_startup(monkeypatch):
    monkeypatch.setattr(warmup.settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 0)

    def broken(sql):
        raise RuntimeError("parser unavailable")

    monkeypatch.setattr(sql_parser, "parse_tree", broken)
    assert warmup.warm_parser().complete


def test_warmup_measurement_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(warmup.settings, "SQL_PARSE_OFFLOAD_MIN_CHARS", 0)
    with caplog.at_level("INFO", logger="app.sql_frontend.warmup"):
        report = warmup.warm_parser()
    assert report.elapsed_ms >= 0
    assert any("SQL parser warmed" in record.getMessage() for record in caplog.records)


def test_api_and_proxy_startup_warm_the_parser():
    from app import main
    from app.proxy import __main__ as proxy_main

    assert "warm_parser()" in inspect.getsource(main._serve)
    assert "warm_parser()" in inspect.getsource(proxy_main._run_admitted)
