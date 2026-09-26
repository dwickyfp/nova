"""Exercise the production Smart worker, access checks, tools and durable journals."""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from collections import defaultdict
from contextlib import ExitStack, suppress
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.benchmark.jev_multidomain.environment import (
    ARTIFACTS,
    authenticated_user,
    isolated_agent_registry,
    isolated_journals,
    public_trace,
)
from tests.benchmark.jev_multidomain.routing_probe import variant_candidates

ACTIVE: ContextVar[dict | None] = ContextVar("jev_benchmark_case", default=None)


class BenchmarkInfrastructureUnavailable(RuntimeError):
    pass


def exception_trace(error: BaseException) -> dict:
    return {
        "type": type(error).__name__,
        "code": error.args[0] if error.args and isinstance(error.args[0], int) else None,
        "frames": [
            {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
            for frame in traceback.extract_tb(error.__traceback__)[-8:]
        ],
    }


class LiveBenchmark:
    def __init__(self, arm: str, *, timeout: float = 240, variant: str = "full") -> None:
        self.arm, self.timeout, self.variant = arm, timeout, variant
        self.environment = json.loads((ARTIFACTS / "environment.json").read_text())
        self.domains = {value["id"]: name for name, value in self.environment["agents"].items()}

    def instruments(self) -> ExitStack:
        from app.modules.agents import agent_control, auto_planner
        from app.modules.ai_ml.decision_settings import DecisionSettings
        from app.modules.assistant import decision
        from app.modules.query.service import query_service

        stack = ExitStack()
        stack.enter_context(isolated_journals())
        allowed = {
            key
            for key, domain in self.domains.items()
            if domain != "SALES" or self.variant == "sales"
        }
        stack.enter_context(isolated_agent_registry(allowed))
        original_candidates = auto_planner.authorized_candidates
        original_post = decision.DecisionSession._post
        original_query = query_service.execute

        async def candidates(user: dict) -> list:
            found = [c for c in await original_candidates(user) if c.agent_id in allowed]
            return variant_candidates(found, self.variant)

        async def session():
            config = DecisionSettings.model_validate(self.environment["decision_settings"])
            config = config.model_copy(update={"enabled": self.arm != "off"})
            if self.arm == "controlled":
                config = config.model_copy(update={"light_model_id": config.heavy_model_id})
            return decision.DecisionSession(config) if config.enabled else None

        async def post(instance, payload: dict, timeout: float) -> dict:
            record = {"request": payload, "timeout_seconds": timeout}
            start = time.monotonic()
            try:
                response = await original_post(instance, payload, timeout)
                record["response"] = response
                return response
            except (Exception, asyncio.CancelledError) as exc:
                record["error"] = type(exc).__name__
                record["http_status"] = getattr(getattr(exc, "response", None), "status_code", None)
                raise
            finally:
                record["latency_seconds"] = time.monotonic() - start
                if ACTIVE.get() is not None:
                    ACTIVE.get()["jev_calls"].append(record)

        async def query(sql: str, *args, **kwargs):
            start = time.monotonic()
            result = await original_query(sql, *args, **kwargs)
            active = ACTIVE.get()
            # Only synthetic data evidence is persisted; credentials and other schemas are excluded.
            if active is not None and self.environment["database"].lower() in sql.lower():
                active["queries"].append(
                    {
                        "sql": sql,
                        "columns": result.columns,
                        "rows": result.rows[:100],
                        "latency_seconds": time.monotonic() - start,
                    }
                )
            return result

        stack.enter_context(patch.object(auto_planner, "authorized_candidates", candidates))
        stack.enter_context(patch.object(agent_control, "authorized_candidates", candidates))
        stack.enter_context(patch.object(decision, "decision_session", session))
        stack.enter_context(patch.object(decision.DecisionSession, "_post", post))
        stack.enter_context(patch.object(query_service, "execute", query))
        return stack

    async def run_case(self, case: dict, thread_id: str | None = None) -> dict:
        from app.modules.agents.harness_repository import TERMINAL
        from app.modules.agents.harness_repository import harness_repository as repo
        from app.modules.agents.harness_worker import AgentHarnessWorker
        from app.modules.agents.identity import SMART_AGENT_ID
        from app.modules.assistant.repository import assistant_repository as messages
        from app.modules.assistant.security import observation_context, session_security

        record = {
            "case_id": case["id"],
            "arm": self.arm,
            "variant": self.variant,
            "question": case["question"],
            "jev_calls": [],
            "queries": [],
        }
        token = ACTIVE.set(record)
        jobs = {}
        started = time.monotonic()
        try:
            user = await authenticated_user()
            security = session_security(user)
            thread = (
                {"thread_id": thread_id}
                if thread_id
                else await messages.create_thread(
                    user_name=user["username"],
                    title=f"JEV benchmark {case['id']}",
                    agent_id=SMART_AGENT_ID,
                )
            )
            history = case.get("history", []) if thread_id is None else []
            for item in [*history, {"role": "user", "content": case["question"]}]:
                latest = await messages.append_message(
                    thread["thread_id"],
                    user_name=user["username"],
                    role=item["role"],
                    content=item["content"],
                    agent_id=SMART_AGENT_ID,
                    security_context=observation_context(security),
                )
            root = await repo.create_root(
                owner_name=user["username"],
                thread_id=thread["thread_id"],
                role_name=user["active_role"],
                session_id=user["session_id"],
                security_version=security.security_context_version,
                objective=case["question"],
                provider_id=self.environment["answer_provider_id"],
                model=self.environment["answer_model"],
                user_message_id=latest["message_id"],
            )
            record["root_id"], record["thread_id"] = root["run_id"], thread["thread_id"]
            worker = AgentHarnessWorker()
            while time.monotonic() - started < self.timeout:
                tree = await repo.tree(
                    root["run_id"], owner_name=user["username"], role_name=user["active_role"]
                )
                root_row = next((r for r in tree if r["run_id"] == root["run_id"]), root)
                if root_row["status"] in TERMINAL:
                    break
                for row in tree:
                    run_id = row["run_id"]
                    if row["status"] == "queued" and (run_id not in jobs or jobs[run_id].done()):
                        jobs[run_id] = asyncio.create_task(
                            worker.process(run_id, f"jev-bench-{uuid4()}")
                        )
                await asyncio.sleep(0.5)
            else:
                record["benchmark_timeout"] = True
                await repo.cancel_tree(root["run_id"])
            for job in jobs.values():
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs.values(), return_exceptions=True)
            tree = await repo.tree(
                root["run_id"], owner_name=user["username"], role_name=user["active_role"]
            )
            record["runs"] = [
                {
                    key: row.get(key)
                    for key in [
                        "run_id",
                        "parent_run_id",
                        "agent_id",
                        "depth",
                        "objective",
                        "status",
                        "result_summary",
                        "error_class",
                        "prompt_tokens",
                        "completion_tokens",
                        "checkpoint",
                    ]
                }
                for row in tree
            ]
            record["selected_agents"] = sorted(
                {self.domains.get(r["agent_id"], "UNKNOWN") for r in tree if r["depth"]}
            )
            root_row = next(r for r in tree if r["run_id"] == root["run_id"])
            record["status"] = root_row["status"]
            record["answer"] = root_row.get("result_summary") or ""
            record["messages"] = await messages.list_messages(
                thread["thread_id"], user_name=user["username"], synchronize=True
            )
            record["events"] = []
            cursor = ""
            for _ in range(20):
                page = await repo.events_after(root["run_id"], cursor, limit=100)
                record["events"].extend(page)
                if len(page) < 100:
                    break
                cursor = str(page[-1]["event_id"])
        except Exception as exc:
            record["harness_error"] = type(exc).__name__
            record["harness_exception"] = exception_trace(exc)
            record["status"] = "harness_error"
        except asyncio.CancelledError:
            if record.get("root_id"):
                with suppress(Exception):
                    await repo.cancel_tree(record["root_id"])
            raise
        finally:
            for job in jobs.values():
                if not job.done():
                    job.cancel()
            outcomes = await asyncio.gather(*jobs.values(), return_exceptions=True)
            record["worker_exceptions"] = [
                exception_trace(outcome)
                for outcome in outcomes
                if isinstance(outcome, Exception)
            ]
            record["latency_seconds"] = time.monotonic() - started
            ACTIVE.reset(token)
        return record

    async def run(self, cases: list[dict], output: Path, concurrency: int = 2) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        saved = {
            row["case_id"]: row
            for row in (
                (json.loads(line) for line in output.read_text().splitlines())
                if output.exists()
                else []
            )
        }
        semaphore = asyncio.Semaphore(concurrency)

        async def group(group_cases: list[dict]) -> None:
            thread_id = None
            async with semaphore:
                for case in group_cases:
                    if case["id"] in saved:
                        result = saved[case["id"]]
                    else:
                        result = await self.run_case(case, thread_id)
                        if result.get("harness_error") in {
                            "OperationalError",
                            "InterfaceError",
                            "ConnectionError",
                            "AgentDiscoveryUnavailable",
                            "AgentMetadataUnavailable",
                        }:
                            with output.with_suffix(".infrastructure.jsonl").open("a") as handle:
                                handle.write(
                                    json.dumps(
                                        public_trace(result), ensure_ascii=False, default=str
                                    )
                                    + "\n"
                                )
                            raise BenchmarkInfrastructureUnavailable(
                                "Infrastructure failed; stop before scoring unexecuted cases"
                            )
                        with output.open("a") as handle:
                            handle.write(
                                json.dumps(public_trace(result), ensure_ascii=False, default=str)
                                + "\n"
                            )
                        print(
                            json.dumps(
                                {
                                    key: result.get(key)
                                    for key in [
                                        "case_id",
                                        "status",
                                        "selected_agents",
                                        "latency_seconds",
                                    ]
                                }
                            ),
                            flush=True,
                        )
                    if case["category"] == "multiturn":
                        thread_id = result.get("thread_id")

        groups = defaultdict(list)
        for case in cases:
            groups[case["group"] if case["category"] == "multiturn" else case["id"]].append(case)
        with self.instruments():
            async with asyncio.TaskGroup() as tasks:
                for items in groups.values():
                    tasks.create_task(group(items))
