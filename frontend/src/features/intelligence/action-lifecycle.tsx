import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { decideToolCall } from "@/features/assistant/stream-client";
import { useAuthStore } from "@/stores/auth-store";
import { actionApi, type BusinessAction } from "./lifecycle-api";

type Props = {
  actionId: string;
  threadId: string;
  canReview?: boolean;
  onChanged?: (action: BusinessAction) => void;
};

export function ActionLifecycle(props: Props) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return (
    <ActionWorkspace
      key={`${epoch}:${props.actionId}`}
      {...props}
      epoch={epoch}
    />
  );
}

function ActionWorkspace({
  actionId,
  threadId,
  canReview = false,
  onChanged,
  epoch,
}: Props & { epoch: number }) {
  const client = useQueryClient();
  const key = ["intelligence", epoch, "action", actionId];
  const [compensationConfirmed, setCompensationConfirmed] = useState(false);
  const [requestActive, setRequestActive] = useState(false);
  const operationIds = useRef(new Map<string, string>());
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => actionApi.get(actionId, signal),
    retry: false,
    staleTime: 0,
    gcTime: 0,
    refetchInterval: (query) =>
      requestActive ||
      ["awaiting_consent", "executing", "compensating"].includes(
        query.state.data?.status ?? "",
      )
        ? 500
        : false,
  });
  const operationId = (name: string, revision: number) => {
    const signature = `${name}:${revision}`;
    if (!operationIds.current.has(signature))
      operationIds.current.set(signature, crypto.randomUUID());
    return operationIds.current.get(signature)!;
  };
  const operate = useMutation({
    mutationFn: (operation: "execute" | "verify" | "compensate" | "cancel") =>
      actionApi.operate(actionId, operation, {
        thread_id: threadId,
        expected_revision: query.data!.revision,
        operation_id: operationId(operation, query.data!.revision),
      }),
    onMutate: () => setRequestActive(true),
    onSuccess: (action) => {
      if (useAuthStore.getState().securityEpoch !== epoch) return;
      client.setQueryData(key, action);
      onChanged?.(action);
      setCompensationConfirmed(false);
    },
    onSettled: () => {
      setRequestActive(false);
      void client.invalidateQueries({ queryKey: key });
    },
  });
  const review = useMutation({
    mutationFn: (operation: "approve" | "deny") =>
      actionApi.review(
        query.data!,
        operation,
        operationId(operation, query.data!.revision),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key });
    },
  });
  const consent = useMutation({
    mutationFn: (decision: "approve" | "deny") =>
      decideToolCall(query.data!.consent_call_id!, decision),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key });
    },
  });
  if (query.isPending)
    return (
      <p role="status" className="text-sm">
        Loading action and its policy…
      </p>
    );
  if (query.isError || !query.data)
    return (
      <div role="alert" className="space-y-3">
        <p className="text-sm text-destructive">
          Action could not be loaded
          {query.error ? `: ${query.error.message}` : "."}
        </p>
        <Button
          variant="outline"
          className="min-h-11"
          onClick={() => void query.refetch()}
        >
          Reload action
        </Button>
      </div>
    );
  const action = query.data;
  const busy = operate.isPending || review.isPending || consent.isPending;
  const error = operate.error ?? review.error ?? consent.error;
  const canDispatch =
    ["approved", "dispatch_ready"].includes(action.status) &&
    action.policy.decision !== "DENY";
  return (
    <section
      aria-label="Action lifecycle"
      className="min-w-0 space-y-5 rounded-md border p-4"
    >
      <div>
        <h3 className="text-base font-medium">Governed monitor action</h3>
        <p role="status" className="mt-1 text-sm">
          {action.status.replace(/_/g, " ")}
        </p>
        <p className="mt-2 break-words text-sm text-muted-foreground">
          {action.expected_effect}
        </p>
      </div>
      <dl className="grid min-w-0 gap-4 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">Business policy</dt>
          <dd className="break-words">
            {action.policy.decision.replace(/_/g, " ")} · revision{" "}
            {action.policy.policy_revision}
            <p className="mt-1">{action.policy.reason}</p>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Reviewer approval</dt>
          <dd className="break-words">
            {action.approval
              ? `${action.approval.actor} · ${action.approval.active_role}`
              : action.policy.decision === "REQUIRE_APPROVAL"
                ? "Required; awaiting an authorized reviewer"
                : "Not required by this policy"}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Tool consent</dt>
          <dd>
            {action.status === "awaiting_consent"
              ? "Awaiting explicit consent for this operation"
              : action.dispatch_attempts
                ? "Dispatch was attempted after consent"
                : "Requested separately before execution"}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Dispatch attempts</dt>
          <dd>{action.dispatch_attempts}</dd>
        </div>
      </dl>
      <div className="space-y-1 text-sm">
        <p className="font-medium">Monitor preview</p>
        <p className="break-words">
          {action.configuration.name} · {action.configuration.value_column}
        </p>
        <p className="text-muted-foreground">
          {action.configuration.enabled
            ? `Schedule every ${action.configuration.cadence_minutes ?? 15} minutes`
            : "Schedule disabled"}{" "}
          · {action.configuration.timezone ?? "Asia/Jakarta"}
        </p>
      </div>
      {action.status === "awaiting_approval" && (
        <div className="space-y-2">
          {canReview ? (
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                className="min-h-11"
                disabled={busy}
                onClick={() => review.mutate("deny")}
              >
                Deny action
              </Button>
              <Button
                className="min-h-11"
                disabled={busy}
                onClick={() => review.mutate("approve")}
              >
                Approve action as reviewer
              </Button>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              An authorized reviewer must approve this action. Your current role
              cannot review it.
            </p>
          )}
        </div>
      )}
      {action.status === "awaiting_consent" && action.consent_call_id && (
        <div className="space-y-3 border-t pt-4">
          <p className="text-sm">
            Allow this specific operation on the reviewed monitor? Consent
            covers this call only.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              className="min-h-11"
              disabled={consent.isPending}
              onClick={() => consent.mutate("deny")}
            >
              Deny tool consent
            </Button>
            <Button
              className="min-h-11"
              disabled={consent.isPending}
              onClick={() => consent.mutate("approve")}
            >
              Allow this operation once
            </Button>
          </div>
        </div>
      )}
      {canDispatch && (
        <Button
          className="min-h-11"
          disabled={busy || !threadId}
          onClick={() => {
            operate.mutate("execute");
            void query.refetch();
          }}
        >
          {operate.isPending
            ? "Requesting execution consent…"
            : "Request execution consent"}
        </Button>
      )}
      {[
        "verification_required",
        "failed",
        "verified",
        "compensation_required",
        "compensated",
      ].includes(action.status) && (
        <Button
          variant="outline"
          className="min-h-11"
          disabled={busy || !threadId}
          onClick={() => operate.mutate("verify")}
        >
          Verify by readback
        </Button>
      )}
      {action.status === "verification_required" && (
        <p className="text-sm text-warning-strong">
          Dispatch outcome is uncertain. Readback verification is required
          before another operation.
        </p>
      )}
      {action.verification && (
        <div className="space-y-1 text-sm">
          <h4 className="font-medium">
            Verification:{" "}
            {action.verification.complete ? "complete" : "incomplete"}
          </h4>
          <p>
            {action.verification.reason.replace(/_/g, " ")} ·{" "}
            {new Date(action.verification.checked_at).toLocaleString()}
          </p>
        </div>
      )}
      {action.receipt && (
        <p className="break-all text-sm text-muted-foreground">
          Monitor {action.receipt.monitor_id} · revision{" "}
          {action.receipt.monitor_revision} · schedule{" "}
          {action.receipt.schedule_enabled ? "enabled" : "disabled"}
        </p>
      )}
      {action.compensation_receipt && (
        <div className="space-y-1 text-sm">
          <h4 className="font-medium">Compensation readback</h4>
          <p className="break-all text-muted-foreground">
            Monitor {action.compensation_receipt.monitor_id} · revision{" "}
            {action.compensation_receipt.monitor_revision} · schedule{" "}
            {action.compensation_receipt.schedule_enabled ? "enabled" : "disabled"}
          </p>
        </div>
      )}
      {(action.status === "verified" ||
        (action.status === "compensation_required" &&
          !action.compensation_attempts)) && (
        <div className="space-y-3 border-t pt-4">
          <p className="text-sm">
            Compensation disables the created monitor and its schedule. It
            requires current authorization and new tool consent.
          </p>
          {compensationConfirmed ? (
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                className="min-h-11"
                disabled={busy}
                onClick={() => setCompensationConfirmed(false)}
              >
                Keep monitor active
              </Button>
              <Button
                className="min-h-11"
                disabled={busy || !threadId}
                onClick={() => {
                  operate.mutate("compensate");
                  void query.refetch();
                }}
              >
                Request compensation consent
              </Button>
            </div>
          ) : (
            <Button
              variant="outline"
              className="min-h-11"
              disabled={busy}
              onClick={() => setCompensationConfirmed(true)}
            >
              Prepare compensation
            </Button>
          )}
        </div>
      )}
      {action.status === "compensation_required" &&
        !!action.compensation_attempts && (
          <p className="text-sm text-warning-strong">
            Compensation was attempted and its outcome is uncertain. Verify the
            monitor and schedule by readback.
          </p>
        )}
      {action.compensation && (
        <p className="text-sm">
          Compensation{" "}
          {action.compensation.complete ? "verified" : "incomplete"}:{" "}
          {action.compensation.reason.replace(/_/g, " ")}
        </p>
      )}
      {[
        "awaiting_approval",
        "approved",
        "dispatch_ready",
        "awaiting_consent",
      ].includes(action.status) && (
        <Button
          variant="outline"
          className="min-h-11"
          disabled={review.isPending || !threadId}
          onClick={() => operate.mutate("cancel")}
        >
          Cancel before dispatch
        </Button>
      )}
      {error && (
        <div role="alert" className="space-y-2">
          <p className="text-sm text-destructive">{error.message}</p>
          <Button
            variant="outline"
            className="min-h-11"
            onClick={() => {
              operate.reset();
              review.reset();
              consent.reset();
              void query.refetch();
            }}
          >
            Reload action
          </Button>
        </div>
      )}
      {action.error_code && (
        <p className="text-sm text-destructive">
          {action.error_code.replace(/_/g, " ")}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        Verification confirms monitoring setup. Business effects require a
        separate observation window and outcome evidence.
      </p>
    </section>
  );
}
