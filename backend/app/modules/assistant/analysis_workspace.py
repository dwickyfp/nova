"""Bounded isolated-analysis contract; no production executor is available."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.deps import get_current_user
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.intelligence.contracts import Contract, Scope
from app.observability.metrics import studio_operation


class AnalysisBounds(Contract):
    timeout_seconds: int = Field(default=30, ge=1, le=60)
    memory_mb: int = Field(default=256, ge=64, le=512)
    cpu_seconds: int = Field(default=10, ge=1, le=30)
    max_input_bytes: int = Field(default=4_194_304, ge=1, le=4_194_304)
    max_output_bytes: int = Field(default=65_536, ge=1, le=262_144)
    max_output_rows: int = Field(default=1000, ge=1, le=1000)
    max_artifacts: int = Field(default=3, ge=0, le=3)
    network: Literal[False] = False
    packages: list[Literal["numpy", "pandas"]] = Field(default_factory=list, max_length=2)


class AnalysisRequest(Contract):
    thread_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    resource_refs: list[str] = Field(default_factory=list, max_length=3)
    language: Literal["python"] = "python"
    code: str = Field(min_length=1, max_length=16000)
    bounds: AnalysisBounds = Field(default_factory=AnalysisBounds)


class AnalysisArtifact(Contract):
    name: str = Field(min_length=1, max_length=255, pattern=r"^[\w .-]+$")
    stage_ref: str = Field(
        min_length=2, max_length=1024, pattern=r"^@[A-Za-z_][A-Za-z0-9_]*[/\w .-]*$"
    )
    media_type: Literal["text/csv", "application/json", "image/png", "text/plain"]
    size_bytes: int = Field(ge=0, le=262_144)
    digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")


class AnalysisResult(Contract):
    execution_id: str
    status: Literal["unavailable", "completed", "cancelled", "timeout", "failed"]
    summary: str = Field(max_length=2000)
    outputs: list[AnalysisArtifact] = Field(default_factory=list, max_length=3)
    rows: list[dict[str, str | float | int | bool | None]] = Field(
        default_factory=list, max_length=1000
    )
    warnings: list[str] = Field(default_factory=list, max_length=10)


class AnalysisCapability(Contract):
    enabled: bool
    available: bool
    isolation: Literal["unavailable", "isolated"]
    reason: str
    bounds: AnalysisBounds = Field(default_factory=AnalysisBounds)


@dataclass(frozen=True)
class AnalysisInput:
    resource_id: str
    name: str
    media_type: str
    content: str


class AnalysisExecutor(Protocol):
    isolated: bool

    async def execute(
        self,
        execution_id: str,
        request: AnalysisRequest,
        scope: Scope,
        inputs: tuple[AnalysisInput, ...],
        cancelled: asyncio.Event,
    ) -> AnalysisResult: ...

    async def cancel(self, execution_id: str, scope: Scope) -> None: ...


class UnavailableAnalysisExecutor:
    isolated = False

    async def execute(self, execution_id, request, scope, inputs, cancelled) -> AnalysisResult:
        return AnalysisResult(
            execution_id=execution_id,
            status="unavailable",
            summary="An isolated analytical workspace is not configured.",
        )

    async def cancel(self, execution_id, scope) -> None:
        return None


STOP_GRACE_SECONDS = 1.0


@dataclass
class ActiveAnalysis:
    scope: Scope
    cancelled: asyncio.Event
    task: asyncio.Task
    stopping: asyncio.Task | None = None


def _consume_result(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


class AnalyticalWorkspace:
    def __init__(self, executor: AnalysisExecutor | None = None) -> None:
        self.executor = executor or UnavailableAnalysisExecutor()
        self.active: dict[str, ActiveAnalysis] = {}

    def capability(self) -> AnalysisCapability:
        enabled = bool(getattr(settings, "STUDIO_ANALYSIS_WORKSPACE_ENABLED", False))
        available = enabled and self.executor.isolated
        return AnalysisCapability(
            enabled=enabled,
            available=available,
            isolation="isolated" if available else "unavailable",
            reason="An isolated executor is configured."
            if available
            else "An isolated analytical workspace is not configured.",
        )

    async def execute(self, request: AnalysisRequest, user: dict) -> AnalysisResult:
        with studio_operation("workspace", "execute"):
            return await self._execute(request, user)

    async def _execute(self, request: AnalysisRequest, user: dict) -> AnalysisResult:
        from app.modules.agents.harness_repository import harness_repository
        from app.modules.agents.mission import require_thread, workflow_scope
        from app.modules.agents.resource_delegation import check_participant, resource_delegation

        scope = workflow_scope(user)
        await require_thread(request.thread_id, user)
        execution_id = str(uuid4())
        if not self.capability().available:
            await self._audit(execution_id, scope, "UNAVAILABLE")
            return await UnavailableAnalysisExecutor().execute(
                execution_id, request, scope, (), asyncio.Event()
            )
        run = await harness_repository.get(request.run_id)
        if run is None or run["thread_id"] != request.thread_id:
            raise HTTPException(status_code=404, detail="Analysis participant not found")
        try:
            check_participant(run, user)
        except ValueError as exc:
            raise HTTPException(
                status_code=403, detail="Analysis participant context changed"
            ) from exc
        if run.get("status") != "running":
            raise HTTPException(status_code=409, detail="Analysis participant is not running")
        # Resolve only already-granted resources; the executor never receives credentials.
        try:
            attachments, refs = await resource_delegation.load(run, user)
        except ValueError as exc:
            raise HTTPException(
                status_code=403, detail="Analysis resources are unavailable"
            ) from exc
        if len(set(request.resource_refs)) != len(request.resource_refs) or not set(
            request.resource_refs
        ) <= set(refs):
            raise HTTPException(status_code=403, detail="Analysis resources are unavailable")
        inputs = tuple(
            AnalysisInput(ref, item["name"], item["media_type"], item["content"])
            for ref, item in zip(refs, attachments, strict=True)
            if ref in request.resource_refs
        )
        if sum(len(item.content.encode()) for item in inputs) > request.bounds.max_input_bytes:
            raise HTTPException(
                status_code=422, detail="Analysis input exceeds the requested bound"
            )
        cancelled = asyncio.Event()
        task = asyncio.create_task(
            self.executor.execute(execution_id, request, scope, inputs, cancelled)
        )
        active = ActiveAnalysis(scope, cancelled, task)
        self.active[execution_id] = active
        cancellation = asyncio.create_task(cancelled.wait())
        try:
            ready, _ = await asyncio.wait(
                {task, cancellation},
                timeout=request.bounds.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled.is_set() or not ready:
                status = "cancelled" if cancelled.is_set() else "timeout"
                stopped = await self._stop(execution_id, active)
                result = AnalysisResult(
                    execution_id=execution_id,
                    status=status if stopped else "failed",
                    summary=(
                        "Analysis cancelled."
                        if status == "cancelled"
                        else "Analysis time limit reached."
                    )
                    if stopped
                    else "Analysis shutdown could not be confirmed.",
                )
                await self._audit(execution_id, scope, result.status.upper())
                return result
            result = task.result()
            current = await harness_repository.get(request.run_id)
            if current is None or current["thread_id"] != request.thread_id:
                raise ValueError("Analysis participant disappeared")
            check_participant(current, user)
            if current.get("status") != "running":
                raise ValueError("Analysis participant stopped")
            _, current_refs = await resource_delegation.load(current, user)
            if not set(request.resource_refs) <= set(current_refs):
                raise ValueError("Analysis resource grants changed")
            result = AnalysisResult.model_validate(result.model_dump())
            if cancelled.is_set():
                stopped = await self._stop(execution_id, active)
                result = AnalysisResult(
                    execution_id=execution_id,
                    status="cancelled" if stopped else "failed",
                    summary="Analysis cancelled."
                    if stopped
                    else "Analysis shutdown could not be confirmed.",
                )
            if (
                result.execution_id != execution_id
                or len(result.outputs) > request.bounds.max_artifacts
                or len(result.rows) > request.bounds.max_output_rows
                or len(result.model_dump_json().encode()) > request.bounds.max_output_bytes
                or sum(o.size_bytes for o in result.outputs) > request.bounds.max_output_bytes
            ):
                raise ValueError("Analysis result violates the output contract")
        except asyncio.CancelledError:
            stopped = await self._stop(execution_id, active)
            await self._audit(execution_id, scope, "CANCELLED" if stopped else "FAILED")
            raise
        except Exception:
            await self._stop(execution_id, active)
            result = AnalysisResult(
                execution_id=execution_id,
                status="failed",
                summary="The isolated analysis could not produce a valid bounded result.",
            )
        finally:
            cancellation.cancel()
            self.active.pop(execution_id, None)
        await self._audit(execution_id, scope, result.status.upper())
        return result

    async def cancel(self, execution_id: str, user: dict) -> dict:
        from app.modules.agents.mission import workflow_scope

        scope = workflow_scope(user)
        active = self.active.get(execution_id)
        if active is None or active.scope != scope:
            raise HTTPException(status_code=404, detail="Analysis execution not found")
        stopped = await self._stop(execution_id, active)
        await self._audit(execution_id, scope, "CANCELLED" if stopped else "FAILED")
        return {"execution_id": execution_id, "status": "cancelled" if stopped else "failed"}

    async def _stop(self, execution_id: str, active: ActiveAnalysis) -> bool:
        async def stop() -> bool:
            active.cancelled.set()
            active.task.cancel()
            shutdown = asyncio.create_task(self.executor.cancel(execution_id, active.scope))
            finished, pending = await asyncio.wait(
                {active.task, shutdown}, timeout=STOP_GRACE_SECONDS
            )
            for task in pending:
                task.cancel()
                task.add_done_callback(_consume_result)
            for task in finished:
                task.add_done_callback(_consume_result)
            return not pending and not shutdown.cancelled() and shutdown.exception() is None

        if active.stopping is None:
            active.stopping = asyncio.create_task(stop())
        return await asyncio.shield(active.stopping)

    @staticmethod
    async def _audit(execution_id: str, scope: Scope, status: str) -> None:
        await write_audit_log(
            event_type="STUDIO_ANALYSIS",
            user_name=scope.principal,
            action="ANALYZE",
            object_type="ANALYSIS",
            object_name=execution_id,
            status=status,
            session_id=scope.session_id,
            active_role=scope.active_role,
            security_context_version=scope.security_context_version,
        )


analytical_workspace = AnalyticalWorkspace()
router = APIRouter(prefix="/studio/analysis-workspace", tags=["Studio analysis"])


@router.get("/capability", response_model=AnalysisCapability)
async def analysis_capability(user: Annotated[dict, Depends(get_current_user)]):
    from app.modules.agents.mission import workflow_scope

    workflow_scope(user)
    return analytical_workspace.capability()


@router.post("/executions", response_model=AnalysisResult)
async def execute_analysis(body: AnalysisRequest, user: Annotated[dict, Depends(get_current_user)]):
    result = await analytical_workspace.execute(body, user)
    if result.status == "unavailable":
        return JSONResponse(status_code=503, content=result.model_dump(mode="json"))
    return result


@router.post("/executions/{execution_id}/cancel")
async def cancel_analysis(execution_id: str, user: Annotated[dict, Depends(get_current_user)]):
    return await analytical_workspace.cancel(execution_id, user)


class AnalyticalWorkspaceTool:
    name = "analysis_workspace"
    description = "Run a bounded analysis using granted attachments in an isolated workspace."
    classification = "read_only"
    requires_consent = False
    parameters = AnalysisRequest.model_json_schema()

    def preview(self, invocation: ToolInvocation) -> str:
        return self.description

    async def run(self, invocation: ToolInvocation, context) -> ToolOutcome:
        if not getattr(context, "user", None):
            return ToolOutcome(
                ok=False, summary="Analysis access denied", error_class="ACCESS_DENIED"
            )
        try:
            body = AnalysisRequest.model_validate(invocation.arguments)
            if body.thread_id != context.thread_id or body.run_id != context.run_id:
                raise ValueError("Analysis cannot change its caller scope")
            result = await analytical_workspace.execute(body, context.user)
        except (ValueError, HTTPException):
            return ToolOutcome(
                ok=False,
                summary="Analysis request rejected",
                error_class="ANALYSIS_REJECTED",
                recoverable=False,
            )
        return ToolOutcome(
            ok=result.status == "completed",
            summary=result.summary,
            data=result.model_dump(mode="json") if result.status == "completed" else None,
            error_class=None
            if result.status == "completed"
            else f"ANALYSIS_{result.status.upper()}",
            recoverable=False,
            safe_detail=result.summary,
        )


analysis_workspace_tool = AnalyticalWorkspaceTool()
