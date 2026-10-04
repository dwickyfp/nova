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

const assertionHints: Record<string, string> = {
  numeric_consistency:
    'Expected object: {"accepted":true,"unsupported_count":0}. The scorer needs recorded numeric facts.',
  tool_selection:
    'Expected object: {"required":["tool_name"],"forbidden":[]}. Choose actual registered tool names.',
  task_completeness:
    'Expected object: {"completed":true}. The scorer checks recorded completion.',
  policy_compliance:
    'Expected object: {"consent_resolved":true,"classification":"read_only"}. Match the operation being tested.',
  evidence_coverage:
    'Expected object: {"minimum":1,"minimum_health":"strong","complete":true}. Missing evidence remains unavailable.',
  latency:
    'Expected object: {"max_ms":10000}. Choose the measured latency budget for this case.',
  efficiency:
    'Expected object: {"tool_calls":10,"provider_calls":10}. Choose the permitted call limits.',
  semantic_selection:
    "Use the expected semantic fields recorded in the trace, such as the selected metric and time range.",
  tool_arguments:
    "Use expected tool arguments recorded by the runtime. Include only public values.",
  clarification_quality:
    "Use expected clarification fields recorded by the runtime.",
  action_verification:
    "Use expected verification fields recorded by the runtime.",
};

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
              `${item.scorer.replace(/_/g, " ")} expectations must be valid JSON.`,
            );
          }
          if (
            !expected ||
            typeof expected !== "object" ||
            Array.isArray(expected)
          )
            throw new Error("Expected values must be a JSON object.");
          return { ...item, expected };
        }),
      };
      if (!body.name || !body.prompt || !body.assertions.length)
        throw new Error("Provide a name, prompt, and at least one assertion.");
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
          ? "Create regression case"
          : initial
            ? "Edit evaluation case"
            : "Create evaluation case"}
      </h3>
      {regression && (
        <p className="text-sm text-muted-foreground">
          Review the case expectations before saving this regression. The
          earlier run keeps its frozen case revision.
        </p>
      )}
      <div className="space-y-2">
        <Label htmlFor={`${prefix}-name`}>Case name</Label>
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
        <Label htmlFor={`${prefix}-prompt`}>User prompt</Label>
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
          Mandatory case
        </label>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <Checkbox
            checked={critical}
            onCheckedChange={(value) => setCritical(value === true)}
          />
          Critical case
        </label>
      </div>
      <p className="text-sm text-muted-foreground">
        Required assertions in mandatory critical cases must all pass before
        promotion. Unavailable evidence cannot pass a required gate.
      </p>
      {assertions.map((item, index) => (
        <fieldset key={index} className="min-w-0 space-y-3 border-t pt-3">
          <legend className="text-sm font-medium">Assertion {index + 1}</legend>
          <div className="space-y-2">
            <Label htmlFor={`${prefix}-scorer-${index}`}>Scorer</Label>
            <Select
              value={item.scorer}
              onValueChange={(value) => change(index, "scorer", value)}
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
                    {scorer.replace(/_/g, " ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${prefix}-expected-${index}`}>
              Expected values (JSON)
            </Label>
            <p
              id={`${prefix}-hint-${index}`}
              className="text-sm text-muted-foreground"
            >
              {assertionHints[item.scorer]}
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
              Required assertion
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
                Remove assertion {index + 1}
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
          Add assertion
        </Button>
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          disabled={save.isPending}
          onClick={onCancel}
        >
          Cancel case edits
        </Button>
        <Button type="submit" className="min-h-11" disabled={save.isPending}>
          {save.isPending ? "Saving case…" : "Save evaluation case"}
        </Button>
      </div>
    </form>
  );
}
