"""Test provider boundary for production-path learning; never an LLM accuracy claim."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from unittest.mock import patch

from app.modules.assistant.provider import AssistantProviderClient, ProviderConfig


@dataclass
class ScriptedBoundary:
    query_plans: dict[str, dict] = field(default_factory=dict)
    statements: dict[str, str] = field(default_factory=dict)
    calls: int = 0
    unavailable: bool = False
    catalog_planning: bool = False

    @contextmanager
    def guard(self):
        from contextlib import ExitStack

        from app.modules.ai_ml.embeddings import EmbeddingError, EmbeddingService

        async def no_embeddings(*_args, **_kwargs):
            raise EmbeddingError("Scripted experiments use lexical memory retrieval")

        with ExitStack() as stack:

            class Patcher:
                @staticmethod
                def setattr(owner, name, value):
                    stack.enter_context(patch.object(owner, name, value))

            self.install(Patcher())
            stack.enter_context(patch.object(EmbeddingService, "embed_batch", no_embeddings))
            yield self

    def plan(self, question: str, catalog: dict) -> dict | None:
        if question in self.query_plans:
            return self.query_plans[question]
        if self.catalog_planning:
            from tests.benchmark.business_intelligence.scripted_planner import plan_from_catalog

            return plan_from_catalog(question, catalog)
        return None

    def install(self, monkeypatch) -> None:
        boundary = self

        async def resolve(_self, **_kwargs):
            return ProviderConfig(
                provider_id="scripted-bi",
                model="scripted-pipeline-v1",
                endpoint="https://benchmark.invalid",
                api_key="fixture",
            )

        async def complete(_self, **kwargs):
            return boundary.answer(**kwargs)

        async def stream(_self, **kwargs):
            answer = boundary.answer(**kwargs)
            if answer.get("content"):
                yield "delta", answer["content"]
            yield "message", answer

        monkeypatch.setattr(AssistantProviderClient, "resolve", resolve)
        monkeypatch.setattr(AssistantProviderClient, "complete", complete)
        monkeypatch.setattr(AssistantProviderClient, "stream", stream)

    def answer(self, *, messages, **_kwargs) -> dict:
        from app.modules.assistant.provider import AssistantProviderError

        self.calls += 1
        if self.unavailable:
            raise AssistantProviderError("Scripted provider unavailable")
        system = str(messages[0].get("content") or "")
        latest = next(
            (row.get("content", "") for row in reversed(messages) if row["role"] == "user"), ""
        )
        try:
            payload = json.loads(latest)
            payload = payload if isinstance(payload, dict) else {}
        except (ValueError, TypeError):
            payload = {}
        if "Extract at most 3 distinct durable facts" in system:
            statement = payload.get("message", "")
            key = self.statements.get(statement)
            value = (
                [
                    {
                        "key": key,
                        "fact": statement,
                        "quote": statement,
                        "existing_id": None,
                        "speech_act": "statement",
                    }
                ]
                if key
                else []
            )
        elif "request" in payload and "tools" in payload:
            question = payload["request"]
            views = (payload.get("semantic_context") or {}).get("views", [])
            candidates = [(view, self.plan(question, view.get("catalog", {}))) for view in views]
            candidates = [(view, plan) for view, plan in candidates if plan]
            view, plan = (
                candidates[0] if len(candidates) == 1 else ({}, self.query_plans.get(question))
            )
            value = {
                "intent": "semantic_analytics" if plan else "clarification",
                "tools": ["semantic_query"] if plan else [],
                "required_tools": ["semantic_query"] if plan else [],
                "skills": [],
                "ml_task": None,
                "intent_frame": {"language": "en"},
                "primary_plan": plan,
                "primary_view": view.get("view"),
            }
        elif "catalog" in payload and "question" in payload:
            value = self.plan(payload["question"], payload["catalog"])
            if value is None:
                raise AssertionError(
                    "No development provider script exists for this semantic request"
                )
        else:
            if latest in self.query_plans and not any(row["role"] == "tool" for row in messages):
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"fixture-{self.calls}",
                            "type": "function",
                            "function": {
                                "name": "semantic_query",
                                "arguments": json.dumps({"question": latest}),
                            },
                        }
                    ],
                }
            return {
                "role": "assistant",
                "content": (
                    "Recorded for review. Published business definitions remain authoritative."
                ),
                "usage": {"prompt_tokens": 30, "completion_tokens": 12},
            }
        return {
            "role": "assistant",
            "content": json.dumps(value),
            "usage": {"prompt_tokens": 40, "completion_tokens": 20},
        }
