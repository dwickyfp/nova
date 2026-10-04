import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { agentsApi, agentVersionsApi, type Agent } from "../api";
import { AgentVersionHistory } from "./version-history";

export function QualityProposalDraft({
  agentId,
  epoch,
  onClose,
}: {
  agentId: string;
  epoch: number;
  onClose: () => void;
}) {
  const query = useQuery({
    queryKey: ["agent-quality", epoch, agentId, "draft-base"],
    queryFn: () => agentsApi.get(agentId),
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
  if (query.isPending)
    return (
      <p role="status" className="text-sm">
        Loading current configuration for a draft…
      </p>
    );
  if (query.isError)
    return (
      <div role="alert" className="space-y-2">
        <p className="text-sm text-destructive">
          Current configuration could not be loaded: {query.error.message}
        </p>
        <Button
          variant="outline"
          className="min-h-11"
          onClick={() => void query.refetch()}
        >
          Reload draft base
        </Button>
      </div>
    );
  return (
    <ProposalDraftForm
      key={query.data.config_revision}
      agent={query.data}
      epoch={epoch}
      onClose={onClose}
    />
  );
}

function ProposalDraftForm({
  agent,
  epoch,
  onClose,
}: {
  agent: Agent;
  epoch: number;
  onClose: () => void;
}) {
  const id = useId();
  const client = useQueryClient();
  const [response, setResponse] = useState(agent.instructions_response);
  const [orchestration, setOrchestration] = useState(
    agent.instructions_orchestration,
  );
  const [versionId, setVersionId] = useState<string | null>(null);
  const changed =
    response !== agent.instructions_response ||
    orchestration !== agent.instructions_orchestration;
  const save = useMutation({
    mutationFn: () =>
      agentVersionsApi.save(agent, {
        name: agent.name,
        instructions_response: response,
        instructions_orchestration: orchestration,
      }),
    onSuccess: (version) => {
      setVersionId(version.version_id);
      void client.invalidateQueries({
        queryKey: ["agent-versions", epoch, agent.agent_id],
      });
      void client.invalidateQueries({
        queryKey: ["agent-quality", epoch, agent.agent_id, "versions"],
      });
    },
  });
  return (
    <form
      className="min-w-0 space-y-4 rounded-md border p-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <h3 className="text-sm font-medium">Prepare a configuration draft</h3>
      <p className="text-sm text-muted-foreground">
        Write the instruction change supported by your reviewed diagnosis.
        Saving creates a draft; evaluation and publication remain separate
        steps. Tools, skills, and Semantic View bindings can be edited in
        Configuration.
      </p>
      <div className="space-y-2">
        <Label htmlFor={`${id}-response`}>Response instructions</Label>
        <Textarea
          id={`${id}-response`}
          value={response}
          onChange={(event) => setResponse(event.target.value)}
          maxLength={16000}
          disabled={save.isPending}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${id}-orchestration`}>
          Orchestration instructions
        </Label>
        <Textarea
          id={`${id}-orchestration`}
          value={orchestration}
          onChange={(event) => setOrchestration(event.target.value)}
          maxLength={16000}
          disabled={save.isPending}
        />
      </div>
      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {save.error.message} Reload the configuration before retrying a
          revision conflict.
        </p>
      )}
      {save.isSuccess && (
        <p role="status" className="text-sm">
          Draft saved. Review and evaluate it in version history before
          publishing.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          className="min-h-11"
          variant="outline"
          disabled={save.isPending}
          onClick={onClose}
        >
          Close draft editor
        </Button>
        <Button
          type="submit"
          className="min-h-11"
          disabled={!changed || save.isPending}
        >
          {save.isPending
            ? "Saving configuration draft…"
            : "Save configuration draft"}
        </Button>
        {versionId && (
          <AgentVersionHistory
            agent={agent}
            requestedVersion={versionId}
            onClose={() => setVersionId(null)}
          />
        )}
      </div>
    </form>
  );
}
