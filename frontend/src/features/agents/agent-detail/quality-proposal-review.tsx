import { useId, useState } from "react";
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
          <Label htmlFor={`${id}-patch`}>Remediation patch</Label>
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
              <p>
                Base revision:{" "}
                <span className="break-all">{patch.base_revision}</span>.
                Evaluate the result before publication.
              </p>
              {!!patch.fields?.length && (
                <p>
                  Changed fields:{" "}
                  {patch.fields
                    .map((field) => field.replace(/_/g, " "))
                    .join(", ")}
                  {patch.source_version_id
                    ? `, restored from release ${patch.source_version_id}`
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
            Regression cases to add
          </legend>
          <p className="text-sm text-muted-foreground">
            Select frozen cases after reviewing their assertions in the
            evaluation.
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
                {candidate.case_id} · revision {candidate.case_revision} ·{" "}
                {candidate.scorers.join(", ")}
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
          Reject proposal
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
          {busy ? "Applying reviewed patch…" : "Accept for draft"}
        </Button>
      </div>
    </div>
  );
}
