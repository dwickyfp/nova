"""Durable, fenced actions composed with the shared assistant consent gate."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.redis import session_store
from app.modules.access_control.business_policy import (
    evaluate_business_policy,
    read_business_policy,
)
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.intelligence.action_contracts import (
    BUSINESS_ACTION_TOOL_DESCRIPTION,
    BUSINESS_ACTION_TOOL_NAME,
    BUSINESS_ACTION_TOOL_PARAMETERS,
    Action,
    ActionApproval,
    ActionEvent,
    ActionOperation,
    ActionPreview,
    ActionReview,
    ActionVerification,
    MonitorPolicyInput,
)
from app.modules.intelligence.contracts import Scope, fingerprint, utc_now
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.intelligence.monitor_action import MonitorActionAdapter
from app.observability.metrics import studio_operation


def require_actions_enabled() -> None:
    if not getattr(settings, "STUDIO_ACTIONS_ENABLED", False):
        raise HTTPException(status_code=503, detail="Business action execution is disabled")


class ActionService:
    def __init__(self, service=None, adapters=None):
        self._service = service
        self.adapters = (
            adapters
            if adapters is not None
            else {
                "monitor-v1": MonitorActionAdapter(service),
            }
        )

    @property
    def service(self):
        if self._service is None:
            from app.modules.intelligence.engine import intelligence_service

            self._service = intelligence_service
        return self._service

    @property
    def repository(self):
        return self.service.repository

    async def revalidate(self, user: dict) -> None:
        scope = Scope.from_user(user)
        session = await session_store.get(scope.session_id) if scope.session_id else None
        if not session or Scope.from_user({**session, "session_id": scope.session_id}) != scope:
            raise HTTPException(status_code=403, detail="Session or active role changed")

    async def get(self, action_id: str, user: dict) -> Action:
        await self.revalidate(user)
        action = await self.service.get("actions", action_id, user)
        if action.scope != Scope.from_user(user):
            raise HTTPException(status_code=404, detail="Action unavailable")
        await self._record_event(action)
        return action

    async def _write(self, action: Action, user: dict, **updates) -> Action:
        await self._record_event(action)
        saved = await self.repository.save(
            "actions",
            action.model_copy(update={**updates, "last_actor": user["username"]}),
            expected_revision=action.revision,
        )
        await self._record_event(saved)
        await self.service._audit("ACTION_" + saved.status.upper(), saved, user)
        return saved

    async def _record_event(self, saved: Action) -> None:
        if not saved.last_actor:
            return
        event = ActionEvent(
            id=fingerprint([saved.id, saved.revision]),
            scope=saved.scope,
            action_id=saved.id,
            decision_id=saved.decision_id,
            action_revision=saved.revision,
            event=saved.status,
            actor=saved.last_actor,
            context_digest=fingerprint(saved.model_dump(mode="json")),
            dispatch_fence=saved.dispatch_fence,
        )
        current = await self.repository.get("action_events", event.id, saved.scope, ActionEvent)
        if current is not None:
            ignored = {"revision", "created_at", "updated_at"}
            if current.model_dump(exclude=ignored) != event.model_dump(exclude=ignored):
                raise HTTPException(status_code=409, detail="Action event requires reconciliation")
            return
        await self.repository.save("action_events", event)

    async def _decision(self, action: Action, user: dict):
        from app.modules.intelligence.decisions import decision_digest

        decision = await self.service.get("decisions", action.decision_id, user)
        if (
            decision.scope != action.scope
            or decision.revision != action.decision_revision
            or decision_digest(decision) != action.decision_digest
        ):
            raise HTTPException(status_code=409, detail="Decision changed; preview a new action")
        if decision.selected_option_id != action.option_id or decision.status not in {
            "selected",
            "approved",
        }:
            raise HTTPException(
                status_code=409, detail="Select and approve the decision before execution"
            )
        from app.modules.intelligence.decisions import current_decision_policy

        current = await current_decision_policy(decision)
        if (
            decision.policy != current
            or current.decision == "DENY"
            or (current.decision == "REQUIRE_APPROVAL" and decision.status != "approved")
        ):
            raise HTTPException(status_code=409, detail="Decision policy or approval changed")
        await self.service.authorize_semantic(action.semantic, user, active=True)
        return decision

    @staticmethod
    async def _policy(action: Action):
        policy = await read_business_policy()
        evaluated = evaluate_business_policy(
            MonitorPolicyInput(),
            policy,
            {
                "action_id": action.id,
                "request_digest": action.request_digest,
                "decision_digest": action.decision_digest,
            },
        )
        return policy, evaluated

    async def _preconditions(self, action: Action, user: dict, *, compensation=False) -> None:
        try:
            await self._validate_preconditions(action, user, compensation=compensation)
        except HTTPException:
            await write_audit_log(
                event_type="INTELLIGENCE",
                user_name=user["username"],
                action="COMPENSATE_ACTION" if compensation else "EXECUTE_ACTION",
                object_type="Action",
                object_name=action.id,
                status="DENIED",
                error_message="Action authorization, policy, or approval refused execution",
                session_id=user.get("session_id"),
                active_role=user.get("active_role"),
                security_context_version=user.get("security_context_version"),
            )
            raise

    async def _validate_preconditions(
        self, action: Action, user: dict, *, compensation=False
    ) -> None:
        await self.revalidate(user)
        await self._decision(action, user)
        policy, evaluated = await self._policy(action)
        if action.policy != evaluated or evaluated.decision == "DENY":
            raise HTTPException(
                status_code=409, detail="Action policy changed; preview and review again"
            )
        if evaluated.decision == "REQUIRE_APPROVAL" and (
            not action.approval
            or action.approval.policy_digest != evaluated.context_digest
            or action.approval.active_role not in policy.reviewer_roles
        ):
            raise HTTPException(
                status_code=403, detail="An authorized reviewer must approve this action"
            )
        if evaluated.decision == "REQUIRE_APPROVAL":
            approval = action.approval
            reviewer = await session_store.get(approval.session_id)
            expected_scope = Scope(
                principal=approval.actor,
                active_role=approval.active_role,
                security_context_version=approval.security_context_version,
                session_id=approval.session_id,
            )
            if (
                not reviewer
                or Scope.from_user({**reviewer, "session_id": approval.session_id})
                != expected_scope
            ):
                raise HTTPException(status_code=403, detail="Reviewer session or role changed")
        if not compensation:
            await self.adapters[action.adapter_id].preview(action.configuration, user)

    async def _check_fence(self, action: Action, lease) -> None:
        if not await lease.renew():
            raise HTTPException(status_code=409, detail="Action lease expired")
        current = await self.repository.get("actions", action.id, action.scope, Action)
        if (
            current is None
            or current.revision != action.revision
            or current.dispatch_fence != action.dispatch_fence
        ):
            raise HTTPException(status_code=409, detail="Action dispatch fence changed")

    async def settle_consent(
        self, action: Action, user: dict, *, denied: bool = False
    ) -> Action:
        async with metadata_lock("action-dispatch:" + action.id, timeout_seconds=120) as lease:
            current = await self.repository.get("actions", action.id, action.scope, Action)
            if current is None:
                raise HTTPException(status_code=404, detail="Action unavailable")
            if (
                current.scope != action.scope
                or current.status != "awaiting_consent"
                or current.consent_call_id != action.consent_call_id
                or current.last_operation_digest != action.last_operation_digest
            ):
                return current
            await self._check_fence(current, lease)
            return await self._write(
                current,
                user,
                status=(current.consent_previous_status or "verification_required")
                if current.consent_compensate
                else ("denied" if denied else "cancelled"),
                consent_call_id=None,
                consent_previous_status=None,
                consent_compensate=False,
            )

    async def preview(self, body: ActionPreview, user: dict) -> Action:
        from app.modules.intelligence.decisions import decision_digest

        require_actions_enabled()
        await self.revalidate(user)
        scope = Scope.from_user(user)
        identity = fingerprint([scope.model_dump(exclude={"session_id"}), body.idempotency_key])
        digest = fingerprint(body.model_dump(mode="json"))
        prior = await self.repository.get("actions", identity, scope, Action)
        if prior:
            if prior.request_digest != digest:
                raise HTTPException(status_code=409, detail="Idempotency key inputs changed")
            return await self.get(identity, user)
        decision = await self.service.get("decisions", body.decision_id, user)
        if decision.scope != scope or decision.revision != body.expected_decision_revision:
            raise HTTPException(
                status_code=409, detail="Review the current owned decision revision"
            )
        if decision.selected_option_id != body.option_id:
            raise HTTPException(
                status_code=422, detail="The action must reference the selected option"
            )
        if (
            body.configuration.semantic != decision.semantic
            or body.configuration.agent_id != decision.agent_id
        ):
            raise HTTPException(
                status_code=422, detail="The monitor must use the decision's governed scope"
            )
        await self.adapters[body.adapter_id].preview(body.configuration, user)
        policy = await read_business_policy()
        evaluated = evaluate_business_policy(
            MonitorPolicyInput(),
            policy,
            {
                "action_id": identity,
                "request_digest": digest,
                "decision_digest": decision_digest(decision),
            },
        )
        action = Action(
            id=identity,
            revision=1,
            scope=scope,
            decision_id=decision.id,
            decision_revision=decision.revision,
            decision_digest=decision_digest(decision),
            semantic=decision.semantic,
            option_id=body.option_id,
            adapter_id=body.adapter_id,
            idempotency_key=body.idempotency_key,
            request_digest=digest,
            configuration=body.configuration,
            policy=evaluated,
            last_actor=user["username"],
            status={
                "ALLOW": "approved",
                "DENY": "denied",
                "REQUIRE_APPROVAL": "awaiting_approval",
            }[evaluated.decision],
        )
        async with metadata_lock("action-preview:" + identity) as lease:
            prior = await self.repository.get("actions", identity, scope, Action)
            if prior and prior.request_digest != digest:
                raise HTTPException(status_code=409, detail="Idempotency key inputs changed")
            if prior is None:
                await self.revalidate(user)
                current = await self.repository.get("decisions", decision.id, scope, type(decision))
                if current is None or decision_digest(current) != decision_digest(decision):
                    raise HTTPException(status_code=409, detail="Decision changed during preview")
                if await read_business_policy() != policy:
                    raise HTTPException(
                        status_code=409, detail="Action policy changed during preview"
                    )
                if not await lease.renew():
                    raise HTTPException(status_code=409, detail="Action preview lease expired")
                saved = await self.repository.save("actions", action)
                await self._record_event(saved)
                await self.service._audit("PREVIEW_ACTION", saved, user)
                return saved
        return await self.get(identity, user)

    async def review(self, action_id: str, body: ActionReview, user: dict) -> dict:
        require_actions_enabled()
        await self.revalidate(user)
        candidate = await self.repository.action_for_review(action_id, Action)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Action unavailable")
        await self._decision(candidate, user)
        async with metadata_lock("action-dispatch:" + action_id, timeout_seconds=120) as lease:
            action = await self.repository.action_for_review(action_id, Action)
            if action is None:
                raise HTTPException(status_code=404, detail="Action unavailable")
            if action.revision != candidate.revision:
                raise HTTPException(status_code=409, detail="Action changed before review")
            await self.revalidate(user)
            await self._decision(action, user)
            policy, evaluated = await self._policy(action)
            if user["active_role"] not in policy.reviewer_roles:
                raise HTTPException(
                    status_code=403, detail="This role cannot review business actions"
                )
            digest = fingerprint(body.model_dump(mode="json"))
            if action.last_operation_id == body.operation_id:
                if action.last_operation_digest != digest:
                    raise HTTPException(status_code=409, detail="Operation inputs changed")
                return {"id": action.id, "revision": action.revision, "status": action.status}
            if (
                action.revision != body.expected_revision
                or action.status != "awaiting_approval"
                or action.policy != evaluated
            ):
                raise HTTPException(
                    status_code=409, detail="Action or policy changed; reload before review"
                )
            await self._check_fence(action, lease)
            saved = await self._write(
                action,
                user,
                status="approved" if body.operation == "approve" else "denied",
                approval=ActionApproval(
                    actor=user["username"],
                    active_role=user["active_role"],
                    security_context_version=Scope.from_user(user).security_context_version,
                    session_id=Scope.from_user(user).session_id,
                    policy_digest=evaluated.context_digest,
                    operation_id=body.operation_id,
                    approved_at=utc_now(),
                )
                if body.operation == "approve"
                else None,
                last_operation_id=body.operation_id,
                last_operation_digest=digest,
            )
            return {"id": saved.id, "revision": saved.revision, "status": saved.status}

    async def dispatch(
        self, action_id: str, body: ActionOperation, user: dict, *, compensate=False
    ) -> Action:
        with studio_operation("action", "execute"):
            return await self._dispatch(action_id, body, user, compensate=compensate)

    async def _dispatch(
        self, action_id: str, body: ActionOperation, user: dict, *, compensate=False
    ) -> Action:
        require_actions_enabled()
        candidate = await self.get(action_id, user)
        async with metadata_lock("action-dispatch:" + action_id, timeout_seconds=120) as lease:
            action = await self.repository.get("actions", action_id, candidate.scope, Action)
            if action is None or action.revision != candidate.revision:
                raise HTTPException(status_code=409, detail="Action changed before dispatch")
            await self.revalidate(user)
            digest = fingerprint(
                [body.model_dump(mode="json", exclude={"expected_revision"}), compensate]
            )
            if (
                action.last_operation_id == body.operation_id
                and action.last_operation_digest != digest
            ):
                raise HTTPException(status_code=409, detail="Operation inputs changed")
            if action.status == "executing":
                await self._check_fence(action, lease)
                return await self._write(action, user, status="verification_required")
            if action.status == "compensating":
                await self._check_fence(action, lease)
                return await self._write(action, user, status="compensation_required")
            if not compensate and (
                action.dispatch_attempts or action.status in {"verified", "verification_required"}
            ):
                return action
            if compensate and action.compensation_attempts:
                return action
            if action.revision != body.expected_revision:
                raise HTTPException(
                    status_code=409, detail="Action changed; reload before execution"
                )
            allowed = (
                {"verified", "verification_required", "compensation_required", "awaiting_consent"}
                if compensate
                else {
                    "approved",
                    "awaiting_consent",
                    "dispatch_ready",
                }
            )
            if action.status not in allowed:
                raise HTTPException(
                    status_code=409, detail="This action cannot execute in its current state"
                )
            if compensate and not action.dispatch_attempts:
                raise HTTPException(status_code=409, detail="This action has not dispatched")
            if action.status == "awaiting_consent" and (
                action.consent_compensate != compensate
                or action.last_operation_id != body.operation_id
                or action.last_operation_digest != digest
            ):
                raise HTTPException(status_code=409, detail="Action consent inputs changed")
            await self._preconditions(action, user, compensation=compensate)
            if not await lease.renew():
                raise HTTPException(status_code=409, detail="Action lease expired")
            action = await self._write(
                action,
                user,
                status="dispatch_ready"
                if not compensate
                else (
                    (action.consent_previous_status or "verification_required")
                    if action.status == "awaiting_consent"
                    else action.status
                ),
                dispatch_fence=uuid4().hex,
                consent_call_id=None,
                consent_previous_status=None,
                consent_compensate=False,
                last_operation_id=body.operation_id,
                last_operation_digest=digest,
            )
            await self._preconditions(action, user, compensation=compensate)
            if not await lease.renew():
                raise HTTPException(status_code=409, detail="Action lease expired before dispatch")
            action = await self._write(
                action,
                user,
                status="compensating" if compensate else "executing",
                **({"compensation_attempts": 1} if compensate else {"dispatch_attempts": 1}),
            )
            try:
                await self._check_fence(action, lease)

                async def guard():
                    await self._preconditions(action, user, compensation=compensate)
                    await self._check_fence(action, lease)

                async with asyncio.timeout(60):
                    adapter = self.adapters[action.adapter_id]
                    receipt = await (
                        adapter.compensate(action, user, guard=guard)
                        if compensate
                        else adapter.execute(action, user, guard=guard)
                    )
                await self._check_fence(action, lease)
                action = await self._write(
                    action,
                    user,
                    **({"compensation_receipt": receipt} if compensate else {"receipt": receipt}),
                    status="compensation_required" if compensate else "verification_required",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Every exception after durable dispatch is ambiguous, including
                # receipt persistence failure. Never replay the external call.
                latest = await self.repository.get("actions", action.id, action.scope, Action)
                if (
                    latest
                    and latest.revision == action.revision
                    and latest.dispatch_fence == action.dispatch_fence
                    and await lease.renew()
                ):
                    return await self._write(
                        latest,
                        user,
                        status="compensation_required" if compensate else "verification_required",
                        error_code="DISPATCH_UNCONFIRMED",
                    )
                raise HTTPException(
                    status_code=409, detail="Dispatch requires verification"
                ) from None
            return await self._verify_locked(action, user, lease, compensated=compensate)

    async def _verify_locked(self, action, user, lease, *, compensated=False):
        with studio_operation("action", "verify"):
            return await self._readback_locked(action, user, lease, compensated=compensated)

    async def _readback_locked(self, action, user, lease, *, compensated=False):
        await self._check_fence(action, lease)
        await self._preconditions(action, user, compensation=compensated)
        try:
            async with asyncio.timeout(30):
                verification = await self.adapters[action.adapter_id].verify(
                    action, user, compensated=compensated
                )
        except HTTPException:
            raise
        except Exception:
            verification = ActionVerification(
                checked_at=utc_now(), complete=False, reason="readback_failed"
            )
        await self._preconditions(action, user, compensation=compensated)
        await self._check_fence(action, lease)
        status = (
            ("compensated" if verification.complete else "compensation_required")
            if compensated
            else ("verified" if verification.complete else "verification_required")
        )
        return await self._write(
            action,
            user,
            status=status,
            error_code=None if verification.complete else "POSTCONDITION_UNCONFIRMED",
            **({"compensation": verification} if compensated else {"verification": verification}),
        )

    async def verify(self, action_id: str, user: dict, *, expected_revision=None) -> Action:
        require_actions_enabled()
        candidate = await self.get(action_id, user)
        async with metadata_lock("action-dispatch:" + action_id, timeout_seconds=120) as lease:
            action = await self.repository.get("actions", action_id, candidate.scope, Action)
            if action is None or action.revision != candidate.revision:
                raise HTTPException(status_code=409, detail="Action changed before verification")
            await self.revalidate(user)
            if expected_revision is not None and action.revision != expected_revision:
                raise HTTPException(status_code=409, detail="Action changed before verification")
            if not action.dispatch_attempts:
                raise HTTPException(status_code=409, detail="This action has not dispatched")
            if action.status == "awaiting_consent":
                raise HTTPException(status_code=409, detail="Resolve pending action consent first")
            if not action.dispatch_fence:
                raise HTTPException(status_code=409, detail="This action has no dispatch fence")
            return await self._verify_locked(
                action, user, lease, compensated=bool(action.compensation_attempts)
            )

    async def cancel(self, action_id: str, body: ActionOperation, user: dict) -> Action:
        candidate = await self.get(action_id, user)
        async with metadata_lock("action-dispatch:" + action_id, timeout_seconds=120) as lease:
            action = await self.repository.get("actions", action_id, candidate.scope, Action)
            if action is None or action.revision != candidate.revision:
                raise HTTPException(status_code=409, detail="Action changed before cancellation")
            await self.revalidate(user)
            if action.status == "cancelled":
                return action
            pending_compensation = (
                action.status == "awaiting_consent" and action.consent_compensate
            )
            if (
                (action.dispatch_attempts and not pending_compensation)
                or action.revision != body.expected_revision
            ):
                raise HTTPException(
                    status_code=409, detail="Dispatched or changed actions require review"
                )
            await self._check_fence(action, lease)
            saved = await self._write(
                action,
                user,
                status=(action.consent_previous_status or "verification_required")
                if pending_compensation
                else "cancelled",
                consent_call_id=None,
                consent_previous_status=None,
                consent_compensate=False,
            )
            if action.consent_call_id:
                from app.modules.assistant.consent import consent_broker

                consent_broker.resolve(action.consent_call_id, None, user_name=user["username"])
            return saved


class BusinessActionTool:
    name = BUSINESS_ACTION_TOOL_NAME
    description = BUSINESS_ACTION_TOOL_DESCRIPTION
    classification = "destructive"
    requires_consent = True
    parameters = BUSINESS_ACTION_TOOL_PARAMETERS

    def __init__(self, service):
        self.service = service

    def preview(self, invocation: ToolInvocation) -> str:
        verb = (
            "Disable the monitor and its schedule"
            if invocation.arguments.get("compensate")
            else "Create the monitor and its schedule"
        )
        return (
            verb + " for the reviewed action " + str(invocation.arguments.get("action_id", ""))[:64]
        )

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        if getattr(context, "collaboration_root", False) or getattr(
            context, "collaboration_tools", ()
        ):
            return ToolOutcome(
                ok=False,
                summary="",
                error_class="POLICY_DENIED",
                error="Smart participants cannot execute business actions.",
            )
        try:
            arguments = dict(invocation.arguments)
            action_id = arguments.pop("action_id")
            compensate = arguments.pop("compensate", False)
            body = ActionOperation.model_validate(arguments)
            if body.thread_id != context.thread_id:
                raise HTTPException(
                    status_code=403, detail="Action consent belongs to another thread"
                )
            from app.modules.agents.router import _require_agent_thread

            record = await self.service.get(action_id, context.user or {})
            await _require_agent_thread(
                body.thread_id, record.configuration.agent_id, context.user_name
            )
            action = await self.service.dispatch(
                action_id, body, context.user or {}, compensate=compensate
            )
            return ToolOutcome(
                ok=action.status in {"verified", "compensated"},
                summary=f"Action {action.status}",
                data={"action_id": action.id, "status": action.status, "revision": action.revision},
                error_class=None
                if action.status in {"verified", "compensated"}
                else "VERIFICATION_REQUIRED",
                error=None
                if action.status in {"verified", "compensated"}
                else "Inspect action verification before continuing.",
            )
        except (HTTPException, ValidationError, KeyError):
            return ToolOutcome(
                ok=False,
                summary="",
                error_class="POLICY_DENIED",
                error="Action inputs, authorization, or policy no longer permit execution.",
            )


action_service = ActionService()
business_action_tool = BusinessActionTool(action_service)


async def supervised_action(
    action_id: str, body: ActionOperation, user: dict, *, compensate=False
) -> Action:
    from app.modules.agents.router import _require_agent_thread
    from app.modules.assistant.consent import ConsentApproval, consent_broker
    from app.modules.assistant.service import LoopContext
    from app.modules.assistant.state import thread_store
    from app.modules.assistant.tool_gate import resolve_tool_consent, validate_tool_invocation
    from app.modules.assistant.tools import invocation_classification

    require_actions_enabled()
    action = await action_service.get(action_id, user)
    thread_row = await _require_agent_thread(
        body.thread_id, action.configuration.agent_id, user["username"]
    )
    thread = thread_store.register(
        thread_id=body.thread_id,
        user_name=user["username"],
        title=thread_row.get("title", ""),
    )
    digest = fingerprint([body.model_dump(mode="json", exclude={"expected_revision"}), compensate])
    if action.last_operation_id == body.operation_id and action.last_operation_digest != digest:
        raise HTTPException(status_code=409, detail="Operation inputs changed")
    if action.compensation_attempts if compensate else action.dispatch_attempts:
        if action.status in {"executing", "compensating"}:
            return await action_service.verify(action_id, user)
        return action
    if action.status == "awaiting_consent":
        if action.consent_compensate != compensate:
            raise HTTPException(status_code=409, detail="Another action operation needs consent")
        if action.consent_call_id and consent_broker.owner_of(action.consent_call_id):
            raise HTTPException(
                status_code=409, detail="This action already has a pending consent request"
            )
        # A broker future is process-local. Reopen an abandoned prompt only
        # after fresh authorization, never by replaying the old approval.
        async with metadata_lock("action-dispatch:" + action.id, timeout_seconds=120) as lease:
            await action_service.revalidate(user)
            current = await action_service.repository.get(
                "actions", action.id, action.scope, Action
            )
            if (
                current is None
                or current.revision != body.expected_revision
                or current.status != "awaiting_consent"
            ):
                raise HTTPException(
                    status_code=409, detail="Action changed during consent recovery"
                )
            await action_service._preconditions(current, user, compensation=compensate)
            await action_service._check_fence(current, lease)
            action = await action_service._write(
                current,
                user,
                status=(current.consent_previous_status or "verification_required")
                if compensate
                else "approved",
                consent_call_id=None,
                consent_previous_status=None,
                consent_compensate=False,
            )
            body = body.model_copy(update={"expected_revision": action.revision})
    if action.revision != body.expected_revision:
        raise HTTPException(
            status_code=409, detail="Action changed; reload before requesting consent"
        )
    await action_service._preconditions(action, user, compensation=compensate)
    eligible = (
        {"verified", "verification_required", "compensation_required"}
        if compensate
        else {
            "approved",
            "dispatch_ready",
        }
    )
    if action.status not in eligible:
        raise HTTPException(status_code=409, detail="This action cannot request execution consent")
    call_id = fingerprint([action.id, action.revision, body.operation_id, compensate])
    async with metadata_lock("action-dispatch:" + action.id, timeout_seconds=120) as lease:
        await action_service.revalidate(user)
        current = await action_service.repository.get("actions", action.id, action.scope, Action)
        if (
            current is None
            or current.revision != body.expected_revision
            or current.status != action.status
        ):
            raise HTTPException(status_code=409, detail="Action changed before consent")
        await action_service._check_fence(current, lease)
        action = await action_service._write(
            current,
            user,
            status="awaiting_consent",
            consent_call_id=call_id,
            consent_previous_status=current.status,
            consent_compensate=compensate,
            last_operation_id=body.operation_id,
            last_operation_digest=digest,
        )
    invocation = ToolInvocation(
        call_id,
        business_action_tool.name,
        {
            "action_id": action.id,
            **body.model_dump(),
            "expected_revision": action.revision,
            "compensate": compensate,
        },
    )
    context = LoopContext(
        user_name=user["username"],
        user=user,
        thread_id=body.thread_id,
        session_id=user.get("session_id"),
        role=user["active_role"],
    )

    async def resolve_consent(call, classification):
        async with metadata_lock("action-dispatch:" + action.id, timeout_seconds=120) as lease:
            await action_service.revalidate(user)
            current = await action_service.repository.get(
                "actions", action.id, action.scope, Action
            )
            await action_service._check_fence(action, lease)
            if current is None or current.consent_call_id != call.tool_call_id:
                raise HTTPException(status_code=409, detail="Action consent request changed")
            future = consent_broker.open(
                call.tool_call_id,
                thread_id=body.thread_id,
                user_name=user["username"],
                classification=classification,
            )
        try:
            return await asyncio.wait_for(future, timeout=90)
        finally:
            consent_broker.resolve(call.tool_call_id, None, user_name=user["username"])

    try:
        errors = validate_tool_invocation(business_action_tool, invocation)
        if errors:
            raise HTTPException(status_code=422, detail="Invalid action tool arguments")
        classification = invocation_classification(business_action_tool, invocation)
        allowed = await resolve_tool_consent(
            business_action_tool,
            invocation,
            classification,
            thread,
            resolve_consent,
            timeout_seconds=90,
        )
        if allowed is True or isinstance(allowed, ConsentApproval):
            outcome = await business_action_tool.run(invocation, context)
        else:
            outcome = ToolOutcome(
                ok=False,
                summary="",
                error_class="CONSENT_DENIED" if allowed is False else "CANCELLED",
            )
    except (TimeoutError, asyncio.CancelledError):
        await action_service.settle_consent(action, user)
        raise
    except Exception:
        await action_service.settle_consent(action, user)
        raise
    await action_service.settle_consent(
        action, user, denied=outcome.error_class == "CONSENT_DENIED"
    )
    return await action_service.get(action.id, user)
