import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import {
  actionApi,
  type BusinessAction,
  type Decision,
  type MonitorConfiguration,
} from "./lifecycle-api";
import { ActionLifecycle } from "./action-lifecycle";

export function MonitorActionPreview(props: {
  decision: Decision;
  configuration: MonitorConfiguration;
  threadId: string;
  canReview?: boolean;
  onCreated?: (action: BusinessAction) => void;
}) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return <Preview key={`${epoch}:${props.decision.id}`} {...props} />;
}

function Preview({
  decision,
  configuration,
  threadId,
  canReview,
  onCreated,
}: {
  decision: Decision;
  configuration: MonitorConfiguration;
  threadId: string;
  canReview?: boolean;
  onCreated?: (action: BusinessAction) => void;
}) {
  const [actionId, setActionId] = useState("");
  const key = useRef<{ signature: string; id: string } | null>(null);
  const preview = useMutation({
    mutationFn: () => {
      if (!decision.selected_option_id)
        throw new Error(
          "Select a decision option before previewing an action.",
        );
      const body = {
        decision_id: decision.id,
        expected_decision_revision: decision.revision,
        option_id: decision.selected_option_id,
        adapter_id: "monitor-v1" as const,
        configuration,
      };
      const signature = JSON.stringify(body);
      if (key.current?.signature !== signature)
        key.current = { signature, id: crypto.randomUUID() };
      return actionApi.preview({ ...body, idempotency_key: key.current.id });
    },
    onSuccess: (action) => {
      setActionId(action.id);
      onCreated?.(action);
    },
  });
  if (actionId)
    return (
      <ActionLifecycle
        actionId={actionId}
        threadId={threadId}
        canReview={canReview}
      />
    );
  return (
    <section className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Create a governed monitor for this decision. Preview evaluates policy
        before any monitor or schedule is created.
      </p>
      <Button
        className="min-h-11"
        disabled={preview.isPending || !decision.selected_option_id}
        onClick={() => preview.mutate()}
      >
        {preview.isPending
          ? "Preparing action preview…"
          : "Preview monitor action"}
      </Button>
      {preview.isError && (
        <p role="alert" className="text-sm text-destructive">
          {preview.error.message}
        </p>
      )}
    </section>
  );
}
