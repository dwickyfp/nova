import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Agent, AutoRunEvent } from "@/features/agents/api";
import { Button } from "@/components/ui/button";
import { autoRunSteps, loadAutoRunEvents } from "./auto-run-timeline";
import { ProcessRail } from "./thought-turn";

export function AutoTurnRail({
  rootRunId,
  agents,
  running,
  onOpenChild,
}: {
  rootRunId: string | null;
  agents: Agent[];
  running: boolean;
  onOpenChild: (childRunId: string) => void;
}) {
  const queryClient = useQueryClient();
  const key = ["studio", "auto-events", rootRunId];
  const query = useQuery({
    queryKey: key,
    queryFn: () => loadAutoRunEvents(rootRunId as string, queryClient.getQueryData<AutoRunEvent[]>(key) ?? []),
    enabled: Boolean(rootRunId),
    refetchInterval: running ? 1500 : false,
  });

  useEffect(() => {
    if (!running && rootRunId) {
      void queryClient.invalidateQueries({ queryKey: ["studio", "auto-events", rootRunId] });
    }
  }, [running, rootRunId, queryClient]);

  if (!rootRunId) {
    return running ? <p className="nova-chat-item text-sm text-muted-foreground">Connecting to Smart…</p> : null;
  }

  if (query.isError && !query.data) {
    return (
      <div role="alert" className="nova-chat-item flex items-center gap-2 text-sm text-muted-foreground">
        <span>Could not load this run&apos;s activity.</span>
        <Button variant="link" size="sm" className="h-auto px-0" onClick={() => void query.refetch()}>Retry</Button>
      </div>
    );
  }

  const steps = autoRunSteps(query.data ?? [], rootRunId, agents);
  if (steps.length === 0) {
    return running ? <p className="nova-chat-item text-sm text-muted-foreground">Waiting for the first run event…</p> : null;
  }
  return <ProcessRail steps={steps} running={running} onOpenChild={onOpenChild} />;
}
