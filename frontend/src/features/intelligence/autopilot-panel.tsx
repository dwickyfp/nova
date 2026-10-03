import { useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { agentsApi } from "@/features/agents/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  SemanticValidation,
  SemanticVersion,
  SemanticViewDetail,
} from "./semantic-views-api";

type Change = {
  kind: string;
  name: string;
  dataset?: string;
  definition?: Record<string, unknown>;
  synonyms?: string[];
  target?: string;
};
type Proposal = {
  proposal_id: string;
  status: string;
  proposal_kind: string;
  proposed_fingerprint: string;
  details: { changes: Change[] };
};
type Preview = {
  proposal: Proposal;
  version: number;
  validation: SemanticValidation;
  prior_definition: unknown;
  proposed_definition: unknown;
};

export function AutopilotPanel(props: {
  view: SemanticViewDetail;
  version: SemanticVersion;
  onVersionCreated: (version: number) => void;
}) {
  const epoch = useAuthStore((s) => s.securityEpoch);
  return (
    <Review
      key={`${epoch}:${props.version.fingerprint}`}
      {...props}
      epoch={epoch}
    />
  );
}

function Review({
  view,
  version,
  onVersionCreated,
  epoch,
}: {
  view: SemanticViewDetail;
  version: SemanticVersion;
  onVersionCreated: (version: number) => void;
  epoch: number;
}) {
  const id = useId();
  const client = useQueryClient();
  const root = `/semantic-views/${encodeURIComponent(view.id)}`;
  const [agentId, setAgentId] = useState("");
  const [sources, setSources] = useState(
    (version.definition.datasets ?? [])
      .map((d) => d.source)
      .filter(Boolean)
      .join(", "),
  );
  const [discovery, setDiscovery] = useState<{
    candidates: Change[];
    evidence: { source: string; method: string; digest: string }[];
  } | null>(null);
  const [kind, setKind] = useState("metric");
  const [name, setName] = useState("");
  const [dataset, setDataset] = useState("");
  const [definition, setDefinition] = useState("{}");
  const [synonyms, setSynonyms] = useState("");
  const [target, setTarget] = useState("metrics");
  const [changes, setChanges] = useState<Change[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [acknowledge, setAcknowledge] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const operation = useRef({ signature: "", id: "" });
  const config = { staleTime: 0, gcTime: 0, retry: false as const };
  const agents = useQuery({
    ...config,
    queryKey: ["autopilot-agents", epoch],
    queryFn: agentsApi.list,
  });
  const bound =
    !agents.isError && !agents.isFetching
      ? (agents.data?.agents.filter((agent) =>
          agent.semantic_view_ids?.includes(view.id),
        ) ?? [])
      : [];
  const activeAgent = agentId || bound[0]?.agent_id || "";
  const proposals = useQuery({
    ...config,
    queryKey: ["autopilot-proposals", epoch, view.id],
    queryFn: () => api.get<{ proposals: Proposal[] }>(`${root}/rule-proposals`),
  });
  const pending =
    !proposals.isError && !proposals.isFetching
      ? (proposals.data?.proposals.filter(
          (p) => p.proposal_kind === "autopilot" && p.status === "pending",
        ) ?? [])
      : [];

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "The operation could not be completed.",
      );
    } finally {
      setBusy(false);
    }
  }

  function edit(candidate: Change) {
    setKind(candidate.kind);
    setName(candidate.name);
    setDataset(candidate.dataset || "");
    setDefinition(JSON.stringify(candidate.definition || {}, null, 2));
    setSynonyms((candidate.synonyms || []).join(", "));
    setTarget(candidate.target || "metrics");
  }

  function addChange() {
    try {
      const value: unknown = JSON.parse(definition);
      if (!value || Array.isArray(value) || typeof value !== "object")
        throw new Error("Use an object for the definition.");
      const change: Change = {
        kind,
        name: name.trim(),
        ...(kind === "dimension" ? { dataset } : {}),
        ...(kind === "synonyms"
          ? {
              synonyms: synonyms
                .split(",")
                .map((v) => v.trim())
                .filter(Boolean),
              target,
            }
          : { definition: value as Record<string, unknown> }),
      };
      setChanges((previous) => [...previous, change]);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Check the definition.");
    }
  }

  async function propose() {
    const body = {
      agent_id: activeAgent,
      base: {
        view_id: view.id,
        version: version.version,
        fingerprint: version.fingerprint,
      },
      changes,
    };
    const signature = JSON.stringify(body);
    if (signature !== operation.current.signature)
      operation.current = { signature, id: crypto.randomUUID() };
    await api.post(`${root}/autopilot/proposals`, {
      ...body,
      operation_id: operation.current.id,
    });
    setChanges([]);
    await proposals.refetch();
  }

  if (version.version !== view.active_version)
    return (
      <p className="text-sm text-muted-foreground">
        Open the published version to discover and propose semantic changes.
      </p>
    );
  return (
    <section className="min-w-0 space-y-5" aria-label="Semantic Autopilot">
      <p className="text-sm text-muted-foreground">
        Discover authorized schema metadata, review proposed definitions, and
        preview validation and regressions before publication.
      </p>
      <div className="space-y-2">
        <Label htmlFor={`${id}-sources`}>Sources, separated by commas</Label>
        <Input
          id={`${id}-sources`}
          value={sources}
          onChange={(e) => setSources(e.target.value)}
          placeholder="database.table"
        />
        <Button
          variant="outline"
          disabled={busy || !sources.trim()}
          onClick={() =>
            void run(async () => {
              setDiscovery(
                await api.post("/semantic-views/autopilot/discover", {
                  sources: sources
                    .split(",")
                    .map((v) => v.trim())
                    .filter(Boolean),
                }),
              );
            })
          }
        >
          Discover candidates
        </Button>
      </div>
      {discovery && (
        <details open>
          <summary className="cursor-pointer text-sm font-medium">
            Discovery candidates
          </summary>
          <div className="mt-2 max-h-64 overflow-y-auto rounded-md border">
            <ul className="divide-y">
              {discovery.candidates.map((candidate, index) => (
                <li
                  key={`${candidate.kind}:${candidate.dataset}:${candidate.name}`}
                  className="flex min-w-0 items-center justify-between gap-2 p-3"
                >
                  <span className="min-w-0 break-words text-sm">
                    {candidate.kind} ·{" "}
                    {candidate.dataset ? `${candidate.dataset}.` : ""}
                    {candidate.name}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => edit(discovery.candidates[index])}
                  >
                    Review
                  </Button>
                </li>
              ))}
            </ul>
          </div>
          <ul className="mt-2 space-y-1 break-all text-xs text-muted-foreground">
            {discovery.evidence.map((e) => (
              <li key={e.source}>
                {e.source} · {e.method} · {e.digest}
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="grid min-w-0 gap-4 rounded-md border p-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor={`${id}-kind`}>Change type</Label>
          <Select value={kind} onValueChange={setKind}>
            <SelectTrigger id={`${id}-kind`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[
                "dataset",
                "relationship",
                "dimension",
                "metric",
                "synonyms",
                "filter",
              ].map((value) => (
                <SelectItem key={value} value={value}>
                  {value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor={`${id}-name`}>Semantic name</Label>
          <Input
            id={`${id}-name`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={128}
          />
        </div>
        {kind === "dimension" && (
          <div className="space-y-2">
            <Label htmlFor={`${id}-dataset`}>Dataset</Label>
            <Input
              id={`${id}-dataset`}
              value={dataset}
              onChange={(e) => setDataset(e.target.value)}
            />
          </div>
        )}
        {kind === "synonyms" ? (
          <>
            <div className="space-y-2">
              <Label htmlFor={`${id}-target`}>Target section</Label>
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger id={`${id}-target`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {["metrics", "datasets", "named_filters"].map((value) => (
                    <SelectItem key={value} value={value}>
                      {value.split("_").join(" ")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${id}-synonyms`}>
                Synonyms, separated by commas
              </Label>
              <Input
                id={`${id}-synonyms`}
                value={synonyms}
                onChange={(e) => setSynonyms(e.target.value)}
              />
            </div>
          </>
        ) : (
          <div className="min-w-0 space-y-2 sm:col-span-2">
            <Label htmlFor={`${id}-definition`}>
              Complete definition (JSON)
            </Label>
            <Textarea
              id={`${id}-definition`}
              value={definition}
              onChange={(e) => setDefinition(e.target.value)}
              className="min-h-40 font-mono text-xs"
              maxLength={50000}
            />
            <p className="text-xs text-muted-foreground">
              An existing definition with this name is replaced in full. Include
              its grain, authority, and other required fields.
            </p>
          </div>
        )}
        <Button
          variant="outline"
          disabled={
            busy ||
            !name.trim() ||
            changes.length >= 30 ||
            (kind === "dimension" && !dataset)
          }
          onClick={addChange}
        >
          Add reviewed change
        </Button>
      </div>
      {changes.length > 0 && (
        <div className="space-y-3">
          <ul className="divide-y rounded-md border">
            {changes.map((change, index) => (
              <li
                key={index}
                className="flex items-center justify-between gap-2 p-3 text-sm"
              >
                <span>
                  {change.kind}: {change.name}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setChanges((values) => values.filter((_, i) => i !== index))
                  }
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
          <Label htmlFor={`${id}-agent`}>Bound agent</Label>
          <Select value={activeAgent} onValueChange={setAgentId}>
            <SelectTrigger id={`${id}-agent`} className="w-full">
              <SelectValue placeholder="Choose an agent" />
            </SelectTrigger>
            <SelectContent>
              {bound.map((agent) => (
                <SelectItem key={agent.agent_id} value={agent.agent_id}>
                  {agent.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            disabled={busy || !activeAgent}
            onClick={() => void run(propose)}
          >
            Save review proposal
          </Button>
        </div>
      )}
      {(agents.isError || proposals.isError) && (
        <p role="alert" className="text-sm text-destructive">
          Agent bindings or proposals could not be loaded.
        </p>
      )}
      {error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="space-y-3">
        <h4 className="font-medium">Pending proposals</h4>
        {pending.map((proposal) => (
          <div
            key={proposal.proposal_id}
            className="space-y-2 rounded-md border p-3"
          >
            <ul className="text-sm">
              {proposal.details.changes.map((change, index) => (
                <li key={index}>
                  {change.kind}: {change.name}
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    setPreview(null);
                    setAcknowledge(false);
                    setPreview(
                      await api.post(
                        `${root}/autopilot/proposals/${proposal.proposal_id}/preview`,
                      ),
                    );
                  })
                }
              >
                Preview validation
              </Button>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await agentsApi.rejectRuleProposal(
                      view.id,
                      proposal.proposal_id,
                    );
                    setPreview(null);
                    await proposals.refetch();
                  })
                }
              >
                Reject proposal
              </Button>
            </div>
          </div>
        ))}
      </div>
      {preview && (
        <section
          className="space-y-3 rounded-md border p-4"
          aria-label="Autopilot preview"
        >
          <h4 className="font-medium">
            Draft version {preview.version}:{" "}
            {preview.validation.valid
              ? "Validation passed"
              : "Changes required"}
          </h4>
          <ul className="text-sm">
            {[...preview.validation.errors, ...preview.validation.warnings].map(
              (message, index) => (
                <li key={index}>{message}</li>
              ),
            )}
          </ul>
          <p className="text-sm">
            Regression changes:{" "}
            {preview.validation.regression?.changed ?? "Unavailable"}
          </p>
          <details>
            <summary className="cursor-pointer text-sm">
              Compare complete definitions
            </summary>
            <div className="mt-2 grid min-w-0 gap-3 lg:grid-cols-2">
              {[
                ["Published", preview.prior_definition],
                ["Proposed", preview.proposed_definition],
              ].map(([label, value]) => (
                <div key={String(label)} className="min-w-0">
                  <p className="text-sm font-medium">{String(label)}</p>
                  <pre className="max-h-80 overflow-auto rounded-md bg-muted p-3 text-xs">
                    {JSON.stringify(value, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          </details>
          {Boolean(preview.validation.regression?.changed) && (
            <div className="flex items-start gap-2">
              <Checkbox
                id={`${id}-ack`}
                checked={acknowledge}
                onCheckedChange={(v) => setAcknowledge(v === true)}
              />
              <Label htmlFor={`${id}-ack`} className="leading-5">
                I reviewed the changed regression results
              </Label>
            </div>
          )}
          <Button
            disabled={
              busy ||
              !preview.validation.valid ||
              (Boolean(preview.validation.regression?.changed) && !acknowledge)
            }
            onClick={() =>
              void run(async () => {
                await api.post(
                  `${root}/autopilot/proposals/${preview.proposal.proposal_id}/approve`,
                  {
                    proposed_fingerprint: preview.proposal.proposed_fingerprint,
                    acknowledge_regressions: acknowledge,
                  },
                );
                await client.invalidateQueries({
                  queryKey: ["intelligence", "semantic"],
                });
                onVersionCreated(preview.version);
                setPreview(null);
                await proposals.refetch();
              })
            }
          >
            Approve and publish this definition
          </Button>
        </section>
      )}
    </section>
  );
}
