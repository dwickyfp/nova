import { useEffect, useId, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleDashed, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge, type StatusTone } from "@/components/ui/status-badge";
import { Textarea } from "@/components/ui/textarea";
import { formatDateTime } from "@/lib/datetime";
import { useAuthStore } from "@/stores/auth-store";
import {
  agentsApi,
  agentVersionsApi,
  type Agent,
  type HarnessGuardRule,
  type HarnessPolicy,
} from "../api";
import {
  EXPERIMENT_STAGES,
  harnessApi,
  type EditRow,
  type HarnessComponent,
  type HarnessExperiment,
  type HarnessLesson,
  type HarnessSurface,
} from "../harness-api";
import { qualityApi } from "../quality-api";
import { toAgentDraft } from "./agent-draft";

const COMPONENT_LABEL: Record<HarnessComponent["key"], string> = {
  instructions: "Instructions",
  standing_rules: "Standing rules",
  tool_guidance: "Tool guidance",
  skills: "Skills",
  procedures: "Procedures",
  guards: "Guards",
  memory: "Memory",
  budget: "Budget",
};

const LOAD_LABEL: Record<HarnessComponent["load"], string> = {
  always: "Every turn, in the prompt",
  on_demand: "Only when used",
  in_loop: "Inside the tool loop",
  retrieved: "Retrieved per turn",
  every_turn: "Every turn, as limits",
};

const VERDICT_TONE: Record<EditRow["verdict"], StatusTone> = {
  helped: "success",
  hurt: "danger",
  confounded: "warning",
  "no observed effect": "neutral",
  "kept baseline": "neutral",
  "no evidence": "neutral",
};

const CANDIDATE_LABEL: Record<EditRow["candidate"], string> = {
  inv: "How it works",
  dom: "Data knowledge",
  compose: "Composed",
};

/** Why an experiment stopped, in words that say what to do next. */
function diagnosisText(item: HarnessExperiment): string {
  switch (item.diagnosis) {
    case "TOKEN_ACCOUNTING_UNAVAILABLE":
      return (
        "Token usage could not be measured for every call" +
        (item.accounting_gaps?.length ? ` (${item.accounting_gaps.join(", ")})` : "") +
        ", so the budget cannot be enforced."
      );
    case "DEPENDENCY_DRIFT":
      return "The baseline version is pinned to tools, models or views that have changed since it was evaluated. Save a new draft version and run the experiment on it.";
    case "RUNTIME_UNAVAILABLE":
      return "The baseline evaluation could not run. Check the Quality tab for the evaluation error.";
    default:
      return "";
  }
}

const EMPTY_POLICY: HarnessPolicy = { standing_rules: [], tool_guidance: {}, guards: [] };

/** Drop blank entries the editor leaves behind; the server validates the rest. */
function cleanPolicy(policy: HarnessPolicy): HarnessPolicy {
  return {
    standing_rules: policy.standing_rules.map((rule) => rule.trim()).filter(Boolean),
    tool_guidance: Object.fromEntries(
      Object.entries(policy.tool_guidance)
        .map(([tool, text]) => [tool, text.trim()] as const)
        .filter(([, text]) => text),
    ),
    guards: policy.guards.map((guard) => ({
      ...guard,
      rule_id: guard.rule_id.trim(),
      message: guard.message.trim(),
      match_any: guard.match_any.map((phrase) => phrase.trim()).filter(Boolean),
    })),
  };
}

const RULE_ID = /^[a-z0-9][a-z0-9_-]{1,47}$/;

/** Problems the server would reject, in words a person can act on. */
function policyProblems(policy: HarnessPolicy): string[] {
  const problems: string[] = [];
  const ids = new Set<string>();
  policy.guards.forEach((guard, index) => {
    const name = guard.rule_id.trim() || `Guard ${index + 1}`;
    if (!RULE_ID.test(guard.rule_id.trim()))
      problems.push(
        `${name}: the rule id needs 2 to 48 lowercase letters, digits, dashes or underscores.`,
      );
    else if (ids.has(guard.rule_id.trim()))
      problems.push(`${name}: another guard already uses this rule id.`);
    ids.add(guard.rule_id.trim());
    const phrases = guard.match_any.map((phrase) => phrase.trim()).filter(Boolean);
    if (phrases.length === 0) problems.push(`${name}: add at least one phrase to match.`);
    else if (phrases.length > 12) problems.push(`${name}: use at most 12 phrases.`);
    if (phrases.some((phrase) => phrase.length < 2 || phrase.length > 120))
      problems.push(`${name}: each phrase needs 2 to 120 characters.`);
    if (guard.message.trim().length < 8)
      problems.push(`${name}: write a message of at least 8 characters for the agent.`);
  });
  return problems;
}

export function AgentHarnessTab({ agentId }: { agentId: string }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const agent = useQuery({
    queryKey: ["agents", "detail", agentId],
    queryFn: () => agentsApi.get(agentId),
  });
  // Publishing or restoring a version changes the agent revision; the census follows it.
  const revision = agent.data ? `${agent.data.config_revision ?? ""}:${agent.data.updated_at}` : "";
  const surface = useQuery({
    queryKey: ["agent-harness", epoch, agentId, revision],
    queryFn: ({ signal }) => harnessApi.surface(agentId, signal),
    enabled: agent.isSuccess || agent.isError,
    placeholderData: (previous) => previous,
  });
  return (
    <div className="min-w-0 space-y-10">
      <section aria-labelledby="harness-surface-heading" className="space-y-3">
        <div className="space-y-1">
          <h2 id="harness-surface-heading" className="text-base font-medium">
            Harness
          </h2>
          <p className="text-sm text-muted-foreground">
            Everything around the model that shapes how this agent works. Text that is
            in the prompt on every turn is capped; a guard acts inside the tool loop,
            so the model cannot skip it.
          </p>
        </div>
        {surface.isPending ? (
          <Skeleton className="h-56 w-full" />
        ) : surface.isError || !surface.data ? (
          <p role="alert" className="text-sm text-destructive">
            The harness could not be loaded.{" "}
            <Button variant="link" onClick={() => void surface.refetch()}>
              Retry harness
            </Button>
          </p>
        ) : (
          <SurfaceSummary surface={surface.data} />
        )}
      </section>
      {surface.data && agent.data ? (
        <PolicyEditor
          key={`${agent.data.config_revision ?? ""}:${agent.data.updated_at}`}
          agent={agent.data}
          surface={surface.data}
        />
      ) : null}
      <Experiments agentId={agentId} />
      <Lessons agentId={agentId} />
    </div>
  );
}

function SurfaceSummary({ surface }: { surface: HarnessSurface }) {
  const { harness_chars, harness_cap } = surface.always_on;
  const share = Math.min(100, Math.round((harness_chars / harness_cap) * 100));
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full min-w-[34rem] text-sm">
          <caption className="sr-only">Harness components</caption>
          <thead className="bg-muted text-left text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">Component</th>
              <th scope="col" className="px-4 py-2 font-medium">Loaded</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">Items</th>
              <th scope="col" className="px-4 py-2 font-medium">Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {surface.components.map((component) => (
              <tr key={component.key}>
                <th scope="row" className="px-4 py-2 text-left font-medium">
                  {COMPONENT_LABEL[component.key]}
                </th>
                <td className="px-4 py-2 text-muted-foreground">
                  {LOAD_LABEL[component.load]}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {component.count ?? "Up to 8"}
                </td>
                <td className="px-4 py-2 text-muted-foreground break-words">
                  {componentDetail(component)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="space-y-1.5">
        <div className="flex items-baseline justify-between gap-3 text-sm">
          <span>Always-on harness text</span>
          <span className="tabular-nums text-muted-foreground">
            {harness_chars.toLocaleString()} of {harness_cap.toLocaleString()} characters
          </span>
        </div>
        <div
          role="meter"
          aria-label="Always-on harness text"
          aria-valuemin={0}
          aria-valuemax={harness_cap}
          aria-valuenow={harness_chars}
          className="h-2 overflow-hidden rounded-full bg-muted"
        >
          <div
            className={share > 85 ? "h-full bg-warning" : "h-full bg-primary"}
            style={{ width: `${share}%` }}
          />
        </div>
      </div>
      {surface.issues.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <CircleCheck className="size-4 text-success-strong" aria-hidden />
          No harness problems found.
        </p>
      ) : (
        <ul className="divide-y rounded-lg border" aria-label="Harness problems">
          {surface.issues.map((issue, index) => (
            <li key={`${issue.component}-${index}`} className="flex gap-3 px-4 py-2.5">
              <CircleAlert
                className={
                  issue.severity === "error"
                    ? "mt-0.5 size-4 shrink-0 text-destructive"
                    : "mt-0.5 size-4 shrink-0 text-warning-strong"
                }
                aria-hidden
              />
              <p className="min-w-0 text-sm break-words">
                <span className="font-medium">{COMPONENT_LABEL[issue.component as HarnessComponent["key"]] ?? issue.component}:</span>{" "}
                {issue.message}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function componentDetail(component: HarnessComponent): string {
  switch (component.key) {
    case "instructions":
    case "standing_rules":
    case "tool_guidance":
      return `${(component.chars ?? 0).toLocaleString()} characters`;
    case "skills":
      return `${component.default?.length ?? 0} default, ${component.discoverable?.length ?? 0} on request`;
    case "procedures":
      return component.names?.length ? component.names.join(", ") : "None selected";
    case "guards":
      return `${component.platform?.length ?? 0} platform, ${component.agent?.length ?? 0} from this agent`;
    case "memory":
      return "Facts this agent learned, scoped to user and role";
    case "budget":
      return `${component.profile ?? "analyst"}: ${component.max_iterations} steps, ${component.time_budget_seconds} s, ${(component.spend_tokens ?? 0).toLocaleString()} tokens per turn`;
  }
}

function PolicyEditor({ agent, surface }: { agent: Agent; surface: HarnessSurface }) {
  const prefix = useId();
  const client = useQueryClient();
  const [policy, setPolicy] = useState<HarnessPolicy>(
    () => structuredClone(agent.harness_policy ?? EMPTY_POLICY),
  );
  const [guidanceTool, setGuidanceTool] = useState("");
  const saved = JSON.stringify(agent.harness_policy ?? EMPTY_POLICY);
  const dirty = JSON.stringify(policy) !== saved;
  const problems = policyProblems(policy);
  const toolOptions = surface.tools;
  const platformGuards =
    surface.components.find((item) => item.key === "guards")?.platform ?? [];

  const save = useMutation({
    mutationFn: () =>
      agentVersionsApi.save(agent, {
        ...toAgentDraft(agent),
        harness_policy: cleanPolicy(policy),
      }),
    onSuccess: () => {
      toast.success("Harness draft saved. Evaluate and publish it from version history.");
      void client.invalidateQueries({ queryKey: ["agent-versions", agent.agent_id] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const setRule = (index: number, value: string) =>
    setPolicy((prev) => ({
      ...prev,
      standing_rules: prev.standing_rules.map((rule, i) => (i === index ? value : rule)),
    }));
  const setGuard = (index: number, patch: Partial<HarnessGuardRule>) =>
    setPolicy((prev) => ({
      ...prev,
      guards: prev.guards.map((guard, i) => (i === index ? { ...guard, ...patch } : guard)),
    }));

  return (
    <section aria-labelledby={`${prefix}-policy`} className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id={`${prefix}-policy`} className="text-base font-medium">
            Rules and guards
          </h2>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Changes are saved as a draft version. Platform policy, permissions and
            consent always apply; these rules can only narrow what the agent does.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={!dirty || save.isPending}
            onClick={() => setPolicy(structuredClone(agent.harness_policy ?? EMPTY_POLICY))}
          >
            Discard
          </Button>
          <Button
            disabled={!dirty || save.isPending || problems.length > 0}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Save draft"}
          </Button>
        </div>
      </div>

      {dirty && problems.length > 0 ? (
        <ul
          aria-label="Fix before saving"
          className="space-y-1 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3"
        >
          {problems.map((problem) => (
            <li key={problem} className="flex gap-2 text-sm">
              <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning-strong" aria-hidden />
              <span className="min-w-0 break-words">{problem}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="space-y-3">
        <h3 className="text-sm font-medium">Standing rules</h3>
        {policy.standing_rules.length === 0 ? (
          <p className="text-sm text-muted-foreground">No standing rules.</p>
        ) : (
          <ul className="space-y-2">
            {policy.standing_rules.map((rule, index) => (
              <li key={index} className="flex items-start gap-2">
                <Label htmlFor={`${prefix}-rule-${index}`} className="sr-only">
                  Standing rule {index + 1}
                </Label>
                <Textarea
                  id={`${prefix}-rule-${index}`}
                  value={rule}
                  maxLength={300}
                  rows={2}
                  onChange={(event) => setRule(index, event.target.value)}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove standing rule ${index + 1}`}
                  onClick={() =>
                    setPolicy((prev) => ({
                      ...prev,
                      standing_rules: prev.standing_rules.filter((_, i) => i !== index),
                    }))
                  }
                >
                  <Trash2 className="size-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
        <Button
          variant="outline"
          size="sm"
          disabled={policy.standing_rules.length >= 12}
          onClick={() =>
            setPolicy((prev) => ({ ...prev, standing_rules: [...prev.standing_rules, ""] }))
          }
        >
          <Plus className="size-4" />
          Add rule
        </Button>
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-medium">Tool guidance</h3>
        {Object.entries(policy.tool_guidance).map(([tool, text]) => (
          <div key={tool} className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor={`${prefix}-guidance-${tool}`}>{tool}</Label>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove guidance for ${tool}`}
                onClick={() =>
                  setPolicy((prev) => {
                    const next = { ...prev.tool_guidance };
                    delete next[tool];
                    return { ...prev, tool_guidance: next };
                  })
                }
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
            <Textarea
              id={`${prefix}-guidance-${tool}`}
              value={text}
              maxLength={600}
              rows={2}
              onChange={(event) =>
                setPolicy((prev) => ({
                  ...prev,
                  tool_guidance: { ...prev.tool_guidance, [tool]: event.target.value },
                }))
              }
            />
          </div>
        ))}
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-guidance-tool`}>Tool</Label>
            <Select value={guidanceTool} onValueChange={setGuidanceTool}>
              <SelectTrigger id={`${prefix}-guidance-tool`} className="w-56">
                <SelectValue placeholder="Choose a tool" />
              </SelectTrigger>
              <SelectContent>
                {toolOptions
                  .filter((tool) => !(tool in policy.tool_guidance))
                  .map((tool) => (
                    <SelectItem key={tool} value={tool}>
                      {tool}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={!guidanceTool}
            onClick={() => {
              setPolicy((prev) => ({
                ...prev,
                tool_guidance: { ...prev.tool_guidance, [guidanceTool]: "" },
              }));
              setGuidanceTool("");
            }}
          >
            <Plus className="size-4" />
            Add guidance
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-medium">Guards</h3>
        <ul className="divide-y rounded-lg border" aria-label="Platform guards">
          {platformGuards.map((guard) => (
            <li key={guard.rule_id} className="flex flex-wrap items-center gap-2 px-4 py-2.5">
              <StatusBadge tone="info">Platform</StatusBadge>
              <code className="text-xs">{guard.rule_id}</code>
              <span className="text-sm text-muted-foreground">{guard.description}</span>
            </li>
          ))}
        </ul>
        {policy.guards.map((guard, index) => (
          <fieldset key={index} className="space-y-3 rounded-lg border p-4">
            <legend className="px-1 text-sm font-medium">
              Guard {guard.rule_id || index + 1}
            </legend>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor={`${prefix}-guard-id-${index}`}>Rule id</Label>
                <Input
                  id={`${prefix}-guard-id-${index}`}
                  value={guard.rule_id}
                  maxLength={48}
                  onChange={(event) =>
                    setGuard(index, { rule_id: event.target.value.toLowerCase() })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${prefix}-guard-stage-${index}`}>When</Label>
                <Select
                  value={guard.stage}
                  onValueChange={(value) =>
                    setGuard(index, { stage: value as HarnessGuardRule["stage"] })
                  }
                >
                  <SelectTrigger id={`${prefix}-guard-stage-${index}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="before_tool">Before a call: block it</SelectItem>
                    <SelectItem value="after_tool_error">After an error: add a hint</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${prefix}-guard-tool-${index}`}>Tool</Label>
                <Select
                  value={guard.tool}
                  onValueChange={(value) => setGuard(index, { tool: value })}
                >
                  <SelectTrigger id={`${prefix}-guard-tool-${index}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="*">Any tool</SelectItem>
                    {toolOptions.map((tool) => (
                      <SelectItem key={tool} value={tool}>
                        {tool}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`${prefix}-guard-match-${index}`}>
                Matches any of these phrases (comma separated, case does not matter)
              </Label>
              <Input
                id={`${prefix}-guard-match-${index}`}
                value={guard.match_any.join(", ")}
                onChange={(event) =>
                  setGuard(index, {
                    match_any: event.target.value.split(",").map((part) => part.trimStart()),
                  })
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`${prefix}-guard-message-${index}`}>
                Message the agent reads
              </Label>
              <Textarea
                id={`${prefix}-guard-message-${index}`}
                value={guard.message}
                maxLength={600}
                rows={2}
                onChange={(event) => setGuard(index, { message: event.target.value })}
              />
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                setPolicy((prev) => ({
                  ...prev,
                  guards: prev.guards.filter((_, i) => i !== index),
                }))
              }
            >
              <Trash2 className="size-4" />
              Remove guard
            </Button>
          </fieldset>
        ))}
        <Button
          variant="outline"
          size="sm"
          disabled={policy.guards.length >= 16}
          onClick={() =>
            setPolicy((prev) => ({
              ...prev,
              guards: [
                ...prev.guards,
                {
                  rule_id: `rule-${prev.guards.length + 1}`,
                  stage: "before_tool",
                  tool: "*",
                  match_any: [],
                  message: "",
                },
              ],
            }))
          }
        >
          <Plus className="size-4" />
          Add guard
        </Button>
      </div>
    </section>
  );
}

function Experiments({ agentId }: { agentId: string }) {
  const prefix = useId();
  const epoch = useAuthStore((state) => state.securityEpoch);
  const client = useQueryClient();
  const key = ["agent-harness-experiments", epoch, agentId];
  const experiments = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => harnessApi.experiments(agentId, signal),
    refetchInterval: (query) =>
      query.state.data?.experiments.some((item) => item.active) ? 4000 : false,
  });
  const versions = useQuery({
    queryKey: ["agent-versions", agentId, "harness"],
    queryFn: () => agentVersionsApi.list(agentId),
  });
  const cases = useQuery({
    queryKey: ["agent-quality", epoch, agentId, "harness-cases"],
    queryFn: ({ signal }) => qualityApi.cases(agentId, signal),
  });
  const [versionId, setVersionId] = useState("");
  const [caseIds, setCaseIds] = useState<string[]>([]);
  const [budget, setBudget] = useState("400000");
  const [openId, setOpenId] = useState("");
  // Only a saved version can be run; an agent that never saved one has none.
  const activeVersion = versions.data?.versions.some(
    (item) => item.version_id === versions.data?.active_version_id,
  )
    ? (versions.data?.active_version_id ?? "")
    : "";
  const chosenVersion = versionId || activeVersion;
  const items = experiments.data?.experiments ?? [];
  const caseItems = cases.data?.items ?? [];
  const budgetValue = Number(budget);
  const budgetValid = Number.isInteger(budgetValue) && budgetValue >= 20000 && budgetValue <= 2000000;
  const running = items.some((item) => item.active);

  useEffect(() => {
    if (!openId && items[0]) setOpenId(items[0].id);
  }, [items, openId]);

  const start = useMutation({
    mutationFn: () =>
      harnessApi.start(agentId, {
        operation_id: `ui-${crypto.randomUUID()}`,
        version_id: chosenVersion,
        case_ids: caseIds,
        max_tokens: budgetValue,
        max_seconds: 3600,
      }),
    onSuccess: (record) => {
      toast.success("Experiment started. It runs in the background.");
      setOpenId(record.id);
      void client.invalidateQueries({ queryKey: key });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <section aria-labelledby={`${prefix}-experiments`} className="space-y-4">
      <div className="space-y-1">
        <h2 id={`${prefix}-experiments`} className="text-base font-medium">
          Refine with an experiment
        </h2>
        <p className="max-w-2xl text-sm text-muted-foreground">
          Runs the selected cases on the current harness, proposes two independent
          changes (how the agent works, and what it knows about the data), runs
          each, composes the best evidence into one harness, and keeps the baseline
          unless a candidate improves without any regression. Nothing is published.
        </p>
      </div>
      <div className="space-y-4 rounded-lg border p-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-version`}>Baseline version</Label>
            <Select value={chosenVersion} onValueChange={setVersionId}>
              <SelectTrigger id={`${prefix}-version`}>
                <SelectValue placeholder="Choose a version" />
              </SelectTrigger>
              <SelectContent>
                {(versions.data?.versions ?? []).map((version) => (
                  <SelectItem key={version.version_id} value={version.version_id}>
                    {version.label} · {formatDateTime(version.created_at)}
                    {version.version_id === activeVersion ? " (active)" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-budget`}>Token budget</Label>
            <Input
              id={`${prefix}-budget`}
              inputMode="numeric"
              value={budget}
              aria-invalid={!budgetValid}
              aria-describedby={`${prefix}-budget-hint`}
              onChange={(event) => setBudget(event.target.value.replace(/[^0-9]/g, ""))}
            />
            <p id={`${prefix}-budget-hint`} className="text-xs text-muted-foreground">
              Between 20,000 and 2,000,000. Each case runs four times.
            </p>
          </div>
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Cases</legend>
          {cases.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : caseItems.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Add quality cases in the Quality tab first.
            </p>
          ) : (
            <ul className="grid gap-2 sm:grid-cols-2">
              {caseItems.map((item) => (
                <li key={item.id} className="flex items-start gap-2">
                  <Checkbox
                    id={`${prefix}-case-${item.id}`}
                    checked={caseIds.includes(item.id)}
                    disabled={!caseIds.includes(item.id) && caseIds.length >= 20}
                    onCheckedChange={(checked) =>
                      setCaseIds((prev) =>
                        checked ? [...prev, item.id] : prev.filter((id) => id !== item.id),
                      )
                    }
                  />
                  <Label htmlFor={`${prefix}-case-${item.id}`} className="font-normal leading-5">
                    {item.name}
                  </Label>
                </li>
              ))}
            </ul>
          )}
        </fieldset>
        <Button
          disabled={
            start.isPending || running || !chosenVersion || caseIds.length === 0 || !budgetValid
          }
          onClick={() => start.mutate()}
        >
          {start.isPending ? "Starting…" : running ? "An experiment is running" : "Start experiment"}
        </Button>
      </div>
      {experiments.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : experiments.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Experiments could not be loaded.{" "}
          <Button variant="link" onClick={() => void experiments.refetch()}>
            Retry experiments
          </Button>
        </p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No experiments yet.</p>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <ExperimentCard
              key={item.id}
              item={item}
              open={openId === item.id}
              onToggle={() => setOpenId(openId === item.id ? "" : item.id)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function experimentTone(item: HarnessExperiment): { tone: StatusTone; label: string } {
  if (item.active) return { tone: "info", label: "Running" };
  if (item.interrupted) return { tone: "warning", label: "Interrupted" };
  if (item.status === "passed") {
    return item.selected_label && item.selected_label !== "baseline"
      ? { tone: "success", label: "Improvement found" }
      : { tone: "neutral", label: "Baseline kept" };
  }
  return { tone: "danger", label: "Incomplete" };
}

function ExperimentCard({
  item,
  open,
  onToggle,
}: {
  item: HarnessExperiment;
  open: boolean;
  onToggle: () => void;
}) {
  const status = experimentTone(item);
  const started = item.started_at ? new Date(item.started_at).toLocaleString() : "";
  return (
    <li className="rounded-lg border">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left"
      >
        <StatusBadge tone={status.tone} dot={item.active}>
          {status.label}
        </StatusBadge>
        <span className="text-sm">
          {item.cases.length} case{item.cases.length === 1 ? "" : "s"}
        </span>
        <span className="text-sm tabular-nums text-muted-foreground">
          {item.total_tokens.toLocaleString()} tokens
        </span>
        <span className="ml-auto text-xs text-muted-foreground">{started}</span>
      </button>
      {open ? <ExperimentDetail item={item} /> : null}
    </li>
  );
}

function ExperimentDetail({ item }: { item: HarnessExperiment }) {
  const ledger = useMemo(
    () => new Map((item.ledger ?? []).map((entry) => [entry.stage, entry])),
    [item.ledger],
  );
  return (
    <div className="space-y-5 border-t px-4 py-4">
      <ol className="space-y-1.5" aria-label="Experiment stages">
        {EXPERIMENT_STAGES.map(([stage, label]) => {
          const done = stage in item.stages;
          const runningNow = item.active && item.inflight === stage;
          const entry = ledger.get(stage);
          const value = item.stages[stage];
          return (
            <li key={stage} className="flex items-center gap-2 text-sm">
              {done ? (
                <CircleCheck className="size-4 text-success-strong" aria-hidden />
              ) : runningNow ? (
                <LoaderCircle className="size-4 animate-spin text-info-strong" aria-hidden />
              ) : (
                <CircleDashed className="size-4 text-muted-foreground" aria-hidden />
              )}
              <span className={done || runningNow ? "" : "text-muted-foreground"}>
                {label}
                {runningNow ? " (running)" : ""}
              </span>
              {done ? (
                <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                  {entry?.reused
                    ? "reused baseline run"
                    : `${(value?.total_tokens ?? 0).toLocaleString()} tokens · ${Math.round((value?.stage_duration_ms ?? 0) / 1000)} s`}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
      {item.status === "unavailable" ? (
        <p role="alert" className="text-sm text-destructive">
          {item.error ?? "The experiment did not finish."}{" "}
          {diagnosisText(item)} The baseline is kept.
        </p>
      ) : null}
      {item.interrupted ? (
        <p className="text-sm text-warning-strong">
          The process running this experiment stopped. Start a new experiment; a
          stage that was in progress is never replayed.
        </p>
      ) : null}
      {item.edit_rows?.length ? (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">What each candidate changed</h3>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[40rem] text-sm">
              <thead className="bg-muted text-left text-muted-foreground">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">Candidate</th>
                  <th scope="col" className="px-3 py-2 font-medium">Changed</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Passing</th>
                  <th scope="col" className="px-3 py-2 font-medium">Contrast</th>
                  <th scope="col" className="px-3 py-2 font-medium">Verdict</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {item.edit_rows.map((row) => (
                  <tr key={row.candidate} className={row.adopted ? "bg-surface-1" : ""}>
                    <th scope="row" className="px-3 py-2 text-left font-medium">
                      {CANDIDATE_LABEL[row.candidate]}
                      {row.adopted ? " (adopted)" : ""}
                    </th>
                    <td className="px-3 py-2 text-muted-foreground">
                      {row.components.length
                        ? row.components.map((name) => name.replace(/_/g, " ")).join(", ")
                        : row.fell_back ? "Proposal rejected" : "Nothing"}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {row.passes} / {row.assertions}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      +{row.improved.length} / −{row.regressed.length}
                    </td>
                    <td className="px-3 py-2">
                      <StatusBadge tone={VERDICT_TONE[row.verdict]}>{row.verdict}</StatusBadge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {item.edit_rows.some((row) => row.notes) ? (
            <dl className="space-y-2 text-sm">
              {item.edit_rows
                .filter((row) => row.notes)
                .map((row) => (
                  <div key={row.candidate}>
                    <dt className="font-medium">{CANDIDATE_LABEL[row.candidate]}</dt>
                    <dd className="text-muted-foreground break-words">{row.notes}</dd>
                  </div>
                ))}
            </dl>
          ) : null}
        </div>
      ) : null}
      {item.rationale ? (
        <p className="text-sm">
          {item.rationale}.
          {item.selected_label && item.selected_label !== "baseline"
            ? " The adopted harness is saved as a version labelled “Harness experiment " +
              item.selected_label +
              "”: evaluate and publish it from version history."
            : ""}
        </p>
      ) : null}
      {item.lesson ? (
        <div className="space-y-1 rounded-lg border p-3 text-sm">
          <p className="font-medium">Lesson recorded</p>
          <p>
            <span className="text-muted-foreground">When </span>
            {item.lesson.cue}
          </p>
          <p>{item.lesson.lesson}</p>
        </div>
      ) : null}
    </div>
  );
}

function Lessons({ agentId }: { agentId: string }) {
  const prefix = useId();
  const epoch = useAuthStore((state) => state.securityEpoch);
  const client = useQueryClient();
  const key = ["agent-harness-lessons", epoch, agentId];
  const lessons = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => harnessApi.lessons(agentId, signal),
  });
  const retire = useMutation({
    mutationFn: (lesson: HarnessLesson) => harnessApi.retireLesson(agentId, lesson.id),
    onSuccess: () => {
      toast.success("Lesson retired. Later experiments no longer read it.");
      void client.invalidateQueries({ queryKey: key });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const items = lessons.data?.lessons ?? [];
  return (
    <section aria-labelledby={`${prefix}-lessons`} className="space-y-3">
      <div className="space-y-1">
        <h2 id={`${prefix}-lessons`} className="text-base font-medium">
          Lesson library
        </h2>
        <p className="max-w-2xl text-sm text-muted-foreground">
          One lesson at most per experiment, written only when measured results
          support it. Later experiments read active lessons before proposing.
        </p>
      </div>
      {lessons.isLoading ? (
        <Skeleton className="h-20 w-full" />
      ) : lessons.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Lessons could not be loaded.{" "}
          <Button variant="link" onClick={() => void lessons.refetch()}>
            Retry lessons
          </Button>
        </p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No lessons yet.</p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {items.map((lesson) => (
            <li key={lesson.id} className="space-y-1.5 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge
                  tone={lesson.confidence === "high" ? "success" : lesson.confidence === "medium" ? "info" : "neutral"}
                >
                  {lesson.confidence} confidence
                </StatusBadge>
                {lesson.retired ? <StatusBadge tone="neutral">Retired</StatusBadge> : null}
                <span className="text-xs text-muted-foreground">
                  {lesson.components.map((name) => name.replace(/_/g, " ")).join(", ")}
                </span>
                {!lesson.retired ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto"
                    disabled={retire.isPending}
                    onClick={() => retire.mutate(lesson)}
                  >
                    Retire
                  </Button>
                ) : null}
              </div>
              <p className="text-sm">
                <span className="text-muted-foreground">When </span>
                {lesson.cue}
              </p>
              <p className="text-sm">{lesson.lesson}</p>
              <p className="text-xs text-muted-foreground break-words">{lesson.evidence}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
