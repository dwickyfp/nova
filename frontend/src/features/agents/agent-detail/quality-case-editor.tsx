import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { Textarea } from "@/components/ui/textarea";
import { qualityApi, qualityScorers, type QualityCase } from "../quality-api";

/** Each check in plain words, with the expected result it compares against. */
export const CHECKS: Record<string, { label: string; hint: string; example: string }> = {
  numeric_consistency: {
    label: "Numbers come from the data",
    hint: "Every number in the answer must match a number the agent actually queried.",
    example: '{"accepted":true,"unsupported_count":0}',
  },
  semantic_selection: {
    label: "Picks the right metric and period",
    hint: "The agent chooses the metric, breakdown and time range you expect.",
    example: '{"metrics":["net_revenue"],"time_range":"last_month"}',
  },
  concept_grounding: {
    label: "Understands the business term",
    hint: 'Words like "profit" map to your business definition, or the agent asks which one you mean ("ambiguous").',
    example: '{"status":"grounded","concepts":["Finance.NetRevenue"]}',
  },
  tool_selection: {
    label: "Uses the right tools",
    hint: "Tools the agent must use, and tools it must not use, by their names.",
    example: '{"required":["semantic_query"],"forbidden":[]}',
  },
  tool_arguments: {
    label: "Passes the right inputs to tools",
    hint: "The inputs the agent sends to a tool, such as a filter value.",
    example: "{}",
  },
  evidence_coverage: {
    label: "Shows its evidence",
    hint: "The answer is backed by query results, not guesses.",
    example: '{"minimum":1,"minimum_health":"strong","complete":true}',
  },
  clarification_quality: {
    label: "Asks a good question when unclear",
    hint: "For an unclear question, the agent asks back instead of guessing.",
    example: "{}",
  },
  task_completeness: {
    label: "Finishes the task",
    hint: "The agent completes what was asked instead of stopping halfway.",
    example: '{"completed":true}',
  },
  policy_compliance: {
    label: "Follows permission rules",
    hint: "Anything that changes data asks for consent first; read-only questions stay read-only.",
    example: '{"consent_resolved":true,"classification":"read_only"}',
  },
  action_verification: {
    label: "Checks an action after doing it",
    hint: "After changing something, the agent confirms the change happened.",
    example: "{}",
  },
  guard_compliance: {
    label: "Respects safety rules",
    hint: "The safety rules (Behavior rules tab) that must, or must not, fire for this question.",
    example: '{"fired":[],"not_fired":[],"max_blocked":0}',
  },
  latency: {
    label: "Answers fast enough",
    hint: "The longest the answer may take, in milliseconds (10000 = 10 seconds).",
    example: '{"max_ms":10000}',
  },
  efficiency: {
    label: "Does not waste steps",
    hint: "The most tool calls and AI calls the agent may use for this question.",
    example: '{"tool_calls":10,"provider_calls":10}',
  },
};

export function checkLabel(scorer: string): string {
  return CHECKS[scorer]?.label ?? scorer.replace(/_/g, " ");
}

export function QualityCaseEditor({
  agentId,
  epoch,
  initial,
  regression = false,
  onSaved,
  onCancel,
}: {
  agentId: string;
  epoch: number;
  initial?: QualityCase;
  regression?: boolean;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const prefix = useId();
  const client = useQueryClient();
  const [name, setName] = useState(
    initial ? `${initial.name}${regression ? " regression" : ""}` : "",
  );
  const [prompt, setPrompt] = useState(initial?.prompt ?? "");
  const [critical, setCritical] = useState(initial?.critical ?? true);
  const [mandatory, setMandatory] = useState(initial?.mandatory ?? true);
  const [assertions, setAssertions] = useState(
    () =>
      initial?.assertions.map((item) => ({
        ...item,
        expected: JSON.stringify(item.expected, null, 2),
      })) ?? [
        {
          scorer: "numeric_consistency",
          required: true,
          expected: '{"accepted":true,"unsupported_count":0}',
        },
      ],
  );
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        prompt: prompt.trim(),
        mandatory,
        critical,
        source: regression
          ? ("regression" as const)
          : (initial?.source ?? ("manual" as const)),
        assertions: assertions.map((item) => {
          let expected: unknown;
          try {
            expected = JSON.parse(item.expected);
          } catch {
            throw new Error(
              `"${checkLabel(item.scorer)}" needs its expected result as valid JSON.`,
            );
          }
          if (
            !expected ||
            typeof expected !== "object" ||
            Array.isArray(expected)
          )
            throw new Error("The expected result must be a JSON object, like {\"max_ms\":10000}.");
          return { ...item, expected };
        }),
      };
      if (!body.name || !body.prompt || !body.assertions.length)
        throw new Error("Add a name, a question, and at least one check.");
      return initial && !regression
        ? qualityApi.updateCase(agentId, initial, body)
        : qualityApi.createCase(agentId, body);
    },
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: ["agent-quality", epoch, agentId, "cases"],
      });
      onSaved();
    },
  });
  const change = (
    index: number,
    key: "scorer" | "expected" | "required",
    value: string | boolean,
  ) =>
    setAssertions((items) =>
      items.map((item, i) => (i === index ? { ...item, [key]: value } : item)),
    );
  return (
    <form
      className="min-w-0 space-y-4 rounded-md border p-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <h3 className="text-base font-medium">
        {regression
          ? "Turn this failure into a test"
          : initial
            ? "Edit test question"
            : "New test question"}
      </h3>
      {regression && (
        <p className="text-sm text-muted-foreground">
          Check what the test expects before saving it. The earlier result
          keeps its own copy of this test.
        </p>
      )}
      <div className="space-y-2">
        <Label htmlFor={`${prefix}-name`}>Name</Label>
        <Input
          id={`${prefix}-name`}
          className="min-h-11"
          required
          maxLength={256}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${prefix}-prompt`}>Question</Label>
        <Textarea
          id={`${prefix}-prompt`}
          required
          maxLength={16000}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </div>
      <div className="flex flex-wrap gap-4">
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <Checkbox
            checked={mandatory}
            onCheckedChange={(value) => setMandatory(value === true)}
          />
          Always run this test
        </label>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <Checkbox
            checked={critical}
            onCheckedChange={(value) => setCritical(value === true)}
          />
          Must pass before publishing
        </label>
      </div>
      <p className="text-sm text-muted-foreground">
        A version can be published only when every required check of a
        must-pass test passes. A check the agent cannot show evidence for does
        not pass.
      </p>
      {assertions.map((item, index) => (
        <fieldset key={index} className="min-w-0 space-y-3 border-t pt-3">
          <legend className="text-sm font-medium">Check {index + 1}</legend>
          <div className="space-y-2">
            <Label htmlFor={`${prefix}-scorer-${index}`}>What to check</Label>
            <Select
              value={item.scorer}
              onValueChange={(value) => {
                // Start from a valid example for the new check, unless the
                // expected result was already edited by hand.
                const untouched =
                  !item.expected.trim() ||
                  item.expected === CHECKS[item.scorer]?.example;
                change(index, "scorer", value);
                if (untouched && CHECKS[value])
                  change(index, "expected", CHECKS[value].example);
              }}
            >
              <SelectTrigger
                id={`${prefix}-scorer-${index}`}
                className="min-h-11 w-full"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {qualityScorers.map((scorer) => (
                  <SelectItem key={scorer} value={scorer}>
                    {checkLabel(scorer)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${prefix}-expected-${index}`}>
              Expected result (JSON)
            </Label>
            <p
              id={`${prefix}-hint-${index}`}
              className="text-sm text-muted-foreground"
            >
              {CHECKS[item.scorer]?.hint}
            </p>
            <Textarea
              id={`${prefix}-expected-${index}`}
              className="font-mono"
              required
              maxLength={16000}
              aria-describedby={`${prefix}-hint-${index}`}
              value={item.expected}
              onChange={(event) =>
                change(index, "expected", event.target.value)
              }
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="flex min-h-11 items-center gap-2 text-sm">
              <Checkbox
                checked={item.required}
                onCheckedChange={(value) =>
                  change(index, "required", value === true)
                }
              />
              This check must pass
            </label>
            {assertions.length > 1 && (
              <Button
                type="button"
                variant="ghost"
                className="min-h-11"
                onClick={() =>
                  setAssertions((items) => items.filter((_, i) => i !== index))
                }
              >
                Remove check {index + 1}
              </Button>
            )}
          </div>
        </fieldset>
      ))}
      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {save.error.message}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          disabled={assertions.length >= 32 || save.isPending}
          onClick={() =>
            setAssertions((items) => [
              ...items,
              { scorer: "task_completeness", required: true, expected: "{}" },
            ])
          }
        >
          Add another check
        </Button>
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          disabled={save.isPending}
          onClick={onCancel}
        >
          Cancel
        </Button>
        <Button type="submit" className="min-h-11" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save test question"}
        </Button>
      </div>
    </form>
  );
}
