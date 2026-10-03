import { useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import {
  agentsApi,
  type AgentMemory,
  type KnowledgeRevision,
} from "@/features/agents/api";
import { semanticViewsApi } from "@/features/intelligence/semantic-views-api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { ContextInspector } from "@/features/intelligence/context-inspector";

type MemoryEvidence = {
  id: string;
  kind: string;
  quote: string;
  source_message_id?: string;
  integrity_hash: string;
};

export function KnowledgePanel({
  agentId,
  memory,
}: {
  agentId: string;
  memory: AgentMemory;
}) {
  const epoch = useAuthStore((s) => s.securityEpoch);
  return (
    <KnowledgeReview
      key={`${epoch}:${memory.memory_id}`}
      agentId={agentId}
      memory={memory}
    />
  );
}

function KnowledgeReview({
  agentId,
  memory,
}: {
  agentId: string;
  memory: AgentMemory;
}) {
  const id = useId();
  const epoch = useAuthStore((s) => s.securityEpoch);
  const queryClient = useQueryClient();
  const [viewId, setViewId] = useState("");
  const [metric, setMetric] = useState("");
  const [definitionKind, setDefinitionKind] = useState<
    "metric" | "filter" | "dimension" | "heuristic"
  >("metric");
  const [selected, setSelected] = useState("");
  const [note, setNote] = useState("");
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const operation = useRef({ signature: "", id: "" });
  const root = `/agents/${encodeURIComponent(agentId)}/memories/${encodeURIComponent(memory.memory_id)}`;
  const context = useMutation({
    mutationFn: () => api.post<{ root_id: string }>(`${root}/context`),
  });
  const config = { staleTime: 0, gcTime: 0, retry: false as const };
  const history = useQuery({
    ...config,
    queryKey: ["knowledge", epoch, agentId, memory.memory_id],
    queryFn: () => api.get<{ items: KnowledgeRevision[] }>(`${root}/revisions`),
  });
  const evidence = useQuery({
    ...config,
    queryKey: ["knowledge-evidence", epoch, agentId, memory.memory_id],
    queryFn: () => api.get<{ items: MemoryEvidence[] }>(`${root}/evidence`),
  });
  const agent = useQuery({
    ...config,
    queryKey: ["knowledge-agent", epoch, agentId],
    queryFn: () => agentsApi.get(agentId),
  });
  const views = agent.data?.semantic_view_ids ?? [];
  const activeView = viewId || views[0] || "";
  const view = useQuery({
    ...config,
    queryKey: ["knowledge-view", epoch, activeView],
    queryFn: () => semanticViewsApi.get(activeView),
    enabled: Boolean(activeView),
  });
  const version =
    !view.isFetching && !view.isError
      ? view.data?.versions.find((v) => v.version === view.data.active_version)
      : undefined;
  const metrics =
    definitionKind === "filter"
      ? (version?.definition.named_filters ?? [])
      : definitionKind === "dimension"
        ? (version?.definition.datasets ?? []).flatMap((dataset) =>
            (dataset.fields ?? [])
              .filter(
                (field) =>
                  field.kind === "dimension" || field.dimension != null,
              )
              .map((field) => ({ name: `${dataset.name}.${field.name}` })),
          )
        : (version?.definition.metrics ?? []);
  const metricName = metric || metrics[0]?.name || "";
  const latest = history.data?.items[0];

  async function review(action: "resolve" | "reject" | "verify") {
    if (!latest) return;
    const body = {
      expected_revision: latest.revision,
      operation: action,
      note: note.trim(),
      ...(action === "resolve" ? { selected_fact: selected } : {}),
      ...(action === "verify" && version
        ? {
            semantic: {
              view_id: activeView,
              version: version.version,
              fingerprint: version.fingerprint,
            },
            definition_kind: definitionKind,
            ...(definitionKind !== "heuristic"
              ? { definition_name: metricName }
              : {}),
            publish_shared: shared,
          }
        : {}),
    };
    const signature = JSON.stringify(body);
    if (operation.current.signature !== signature)
      operation.current = { signature, id: crypto.randomUUID() };
    setBusy(true);
    setMessage("");
    try {
      await api.post(`${root}/review`, {
        ...body,
        operation_id: operation.current.id,
      });
      context.reset();
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["knowledge", epoch, agentId, memory.memory_id],
        }),
        queryClient.invalidateQueries({
          queryKey: ["agents", agentId, "memories"],
        }),
      ]);
      setMessage("Review saved.");
    } catch {
      setMessage(
        "Review could not be saved. Refresh the evidence and check your permission before retrying.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (history.isFetching || agent.isFetching)
    return (
      <p className="text-sm text-muted-foreground" role="status">
        Checking knowledge and access…
      </p>
    );
  if (history.isError || agent.isError || !latest)
    return (
      <div role="alert" className="space-y-2 text-sm">
        <p>Knowledge is unavailable under your current access.</p>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            void history.refetch();
            void agent.refetch();
          }}
        >
          Retry
        </Button>
      </div>
    );

  return (
    <section
      aria-label="Knowledge review"
      className="min-w-0 space-y-4 rounded-md border p-3 text-sm"
    >
      <div>
        <h3 className="font-medium">
          {latest.state.toLowerCase()} · Revision {latest.revision}
        </h3>
        <p className="text-muted-foreground">
          {latest.visibility === "DOMAIN"
            ? "Shared reviewed knowledge"
            : "Private knowledge"}{" "}
          · {latest.authority.split("_").join(" ")}
        </p>
        {latest.needs_revalidation && (
          <p role="status">This knowledge needs revalidation.</p>
        )}
        <p className="text-muted-foreground">
          Last source observation:{" "}
          {latest.last_observed_at
            ? new Date(latest.last_observed_at).toLocaleString()
            : "unavailable"}
          . Knowledge confidence score:{" "}
          {latest.confidence?.value == null
            ? "unavailable"
            : latest.confidence.value.toLocaleString()}
          .
        </p>
      </div>
      {latest.alternatives.length > 0 && (
        <div className="space-y-2">
          <Label htmlFor={`${id}-alternative`}>Competing definitions</Label>
          <Select value={selected} onValueChange={setSelected}>
            <SelectTrigger id={`${id}-alternative`} className="w-full">
              <SelectValue placeholder="Select the supported statement" />
            </SelectTrigger>
            <SelectContent>
              {[latest.fact, ...latest.alternatives].map((fact) => (
                <SelectItem
                  key={fact}
                  value={fact}
                  className="max-w-full whitespace-normal"
                >
                  {fact}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !note.trim() || !selected}
            onClick={() => void review("resolve")}
          >
            Resolve as unverified
          </Button>
        </div>
      )}
      <div className="space-y-2">
        <Label htmlFor={`${id}-note`}>Review evidence and reason</Label>
        <Textarea
          id={`${id}-note`}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={2000}
        />
      </div>
      {views.length > 0 && (
        <div className="space-y-3 border-t pt-3">
          <p>
            Published definitions supply calculations. Reviewed heuristics
            retain their source statement and cannot authorize actions.
          </p>
          <Label htmlFor={`${id}-kind`}>Knowledge type</Label>
          <Select
            value={definitionKind}
            onValueChange={(value) => {
              setDefinitionKind(value as typeof definitionKind);
              setMetric("");
            }}
          >
            <SelectTrigger id={`${id}-kind`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="metric">Published metric</SelectItem>
              <SelectItem value="filter">Published filter</SelectItem>
              <SelectItem value="dimension">Published dimension</SelectItem>
              <SelectItem value="heuristic">Business heuristic</SelectItem>
            </SelectContent>
          </Select>
          <Label htmlFor={`${id}-view`}>Published Semantic View</Label>
          <Select
            value={activeView}
            onValueChange={(value) => {
              setViewId(value);
              setMetric("");
            }}
          >
            <SelectTrigger id={`${id}-view`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {views.map((value) => (
                <SelectItem key={value} value={value}>
                  {value === activeView && view.data ? view.data.name : value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {view.isError ? (
            <p role="alert">Published definitions are unavailable.</p>
          ) : definitionKind !== "heuristic" ? (
            <>
              <Label htmlFor={`${id}-metric`}>
                {definitionKind === "metric"
                  ? "Metric"
                  : definitionKind === "filter"
                    ? "Named filter"
                    : "Dimension"}
              </Label>
              <Select value={metricName} onValueChange={setMetric}>
                <SelectTrigger
                  id={`${id}-metric`}
                  className="w-full"
                  disabled={!version}
                >
                  <SelectValue placeholder="Select a published definition" />
                </SelectTrigger>
                <SelectContent>
                  {metrics.map((item) => (
                    <SelectItem key={item.name} value={item.name}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </>
          ) : (
            <p className="text-muted-foreground">
              The Semantic View owner must review supporting evidence. Resolve
              competing statements before verifying a heuristic.
            </p>
          )}
          <div className="flex items-start gap-2">
            <Checkbox
              id={`${id}-shared`}
              checked={shared}
              onCheckedChange={(value) => setShared(value === true)}
            />
            <Label htmlFor={`${id}-shared`} className="leading-5">
              Share verified knowledge with authorized agents using this
              Semantic View
            </Label>
          </div>
          <Button
            size="sm"
            disabled={
              busy ||
              !note.trim() ||
              !version ||
              (definitionKind === "heuristic"
                ? latest.state === "CONFLICTED" ||
                  latest.state === "REJECTED" ||
                  latest.state === "SUPERSEDED" ||
                  !latest.evidence_ids.length
                : !metricName)
            }
            onClick={() => void review("verify")}
          >
            {definitionKind === "heuristic"
              ? "Verify reviewed heuristic"
              : "Verify published definition"}
          </Button>
        </div>
      )}
      <Button
        size="sm"
        variant="outline"
        disabled={busy || !note.trim()}
        onClick={() => void review("reject")}
      >
        Reject knowledge
      </Button>
      {message && <p role="status">{message}</p>}
      <Button
        size="sm"
        variant="outline"
        disabled={context.isPending}
        onClick={() => context.mutate()}
      >
        {context.isPending
          ? "Preparing knowledge context…"
          : "Inspect knowledge context"}
      </Button>
      {context.isError && (
        <p role="alert">
          Knowledge context could not be loaded. Check access and retry.
        </p>
      )}
      {context.data && !context.isPending && !context.isError && (
        <ContextInspector
          semantic={latest.semantic ?? undefined}
          initialNodeId={context.data.root_id}
        />
      )}
      <details>
        <summary className="cursor-pointer">
          Evidence and revision history
        </summary>
        <div className="mt-3 max-h-64 space-y-3 overflow-y-auto break-words">
          {evidence.isFetching ? (
            <p>Checking evidence…</p>
          ) : evidence.isError ? (
            <p role="alert">Evidence could not be loaded.</p>
          ) : (
            evidence.data?.items.map((item) => (
              <blockquote key={item.id} className="border-s-2 ps-3">
                <p>{item.quote}</p>
                <p className="text-xs text-muted-foreground">
                  {item.kind.split("_").join(" ")} ·{" "}
                  {item.source_message_id || item.id}
                </p>
              </blockquote>
            ))
          )}
          {history.data?.items.map((item) => (
            <div key={item.revision}>
              <p>
                Revision {item.revision} · {item.state.toLowerCase()}
              </p>
              <p>{item.fact}</p>
              {item.review_note && (
                <p className="text-muted-foreground">{item.review_note}</p>
              )}
            </div>
          ))}
        </div>
      </details>
    </section>
  );
}
