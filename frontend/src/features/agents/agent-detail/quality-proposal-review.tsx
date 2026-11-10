import { useId, useState } from "react";
import { checkLabel } from "./quality-case-editor";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { QualityProposal } from "../quality-api";

export function QualityProposalReview({
  proposal,
  busy,
  onReview,
}: {
  proposal: QualityProposal;
  busy: boolean;
  onReview: (
    resolution: "accepted" | "rejected",
    options?: { patch_id?: string; regression_case_ids?: string[] },
  ) => void;
}) {
  const id = useId();
  const [patchId, setPatchId] = useState(proposal.patches?.[0]?.id);
  const [cases, setCases] = useState<string[]>([]);
  const patch = proposal.patches?.find((item) => item.id === patchId);
  if (proposal.application?.status === "pending")
    return (
      <div className="space-y-2">
        <p className="text-sm">
          The reviewed application is pending. Resume it to recover the same
          draft or Semantic proposal.
        </p>
        <Button
          className="min-h-11"
          disabled={busy}
          onClick={() => onReview("accepted")}
        >
          {busy ? "Resuming application…" : "Resume reviewed application"}
        </Button>
      </div>
    );
  return (
    <div className="min-w-0 space-y-3">
      {!!proposal.patches?.length && (
        <div className="space-y-2">
          <Label htmlFor={`${id}-patch`}>Suggested change</Label>
          <Select value={patchId} onValueChange={setPatchId} disabled={busy}>
            <SelectTrigger id={`${id}-patch`} className="min-h-11 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {proposal.patches.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.description}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {patch && (
            <div className="space-y-2 text-sm">
              <p className="text-muted-foreground">
                Applying it creates a draft from version{" "}
                <span className="font-mono">{patch.base_revision.slice(0, 8)}</span>.
                Test the draft before you publish it.
              </p>
              {!!patch.fields?.length && (
                <p>
                  It changes{" "}
                  {patch.fields
                    .map(
                      (field) =>
                        ({
                          instructions_response: "how the agent writes answers",
                          instructions_orchestration: "how the agent plans its work",
                        })[field] ?? field.replace(/_/g, " "),
                    )
                    .join(" and ")}
                  {patch.source_version_id
                    ? `, as it was in version ${patch.source_version_id.slice(0, 8)}`
                    : ""}
                  .
                </p>
              )}
              {patch.instruction && (
                <p className="whitespace-pre-wrap break-words rounded-md border p-3">
                  {patch.instruction}
                </p>
              )}
              {patch.semantic && (
                <p>
                  Semantic View {patch.semantic.view_id} · version{" "}
                  {patch.semantic.version}
                </p>
              )}
              {patch.changes?.map((change, index) => (
                <p key={index}>
                  {change.name}:{" "}
                  {change.synonyms?.length
                    ? `Keep synonyms ${change.synonyms.join(", ")}`
                    : "Remove ambiguous synonyms"}
                  .
                </p>
              ))}
            </div>
          )}
        </div>
      )}
      {!!proposal.regression_candidates?.length && (
        <fieldset className="space-y-2" disabled={busy}>
          <legend className="text-sm font-medium">
            Keep testing these questions
          </legend>
          <p className="text-sm text-muted-foreground">
            Tick the questions that failed so every future version is tested
            on them too.
          </p>
          {proposal.regression_candidates.map((candidate) => (
            <div key={candidate.id} className="flex items-start gap-2">
              <Checkbox
                id={`${id}-${candidate.id}`}
                checked={cases.includes(candidate.id)}
                onCheckedChange={(checked) =>
                  setCases((selected) =>
                    checked === true
                      ? [...selected, candidate.id]
                      : selected.filter((item) => item !== candidate.id),
                  )
                }
              />
              <Label
                htmlFor={`${id}-${candidate.id}`}
                className="min-h-11 break-words"
              >
                <span>
                  Failed test · {candidate.scorers.map(checkLabel).join(", ")}
                </span>{" "}
                <span className="font-mono text-xs text-muted-foreground">
                  {candidate.case_id.slice(0, 8)}
                </span>
              </Label>
            </div>
          ))}
        </fieldset>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          className="min-h-11"
          disabled={busy}
          onClick={() => onReview("rejected")}
        >
          Dismiss
        </Button>
        <Button
          className="min-h-11"
          disabled={busy}
          onClick={() =>
            onReview(
              "accepted",
              patchId
                ? { patch_id: patchId, regression_case_ids: cases }
                : undefined,
            )
          }
        >
          {busy ? "Applying…" : "Apply as a draft"}
        </Button>
      </div>
    </div>
  );
}
