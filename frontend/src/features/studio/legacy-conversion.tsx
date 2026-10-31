import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Archive, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { studioApi, type StudioArtifactConversion } from "@/features/agents/api";

/**
 * Views saved before BI are read-only until they are converted. Conversion
 * keeps each view's id and runs as the signed-in user; a view that uses
 * something BI computes differently is kept until its owner converts it
 * anyway.
 */

function useConvertArtifacts(onReport?: (outcomes: StudioArtifactConversion[]) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { artifact_ids?: string[]; force?: boolean }) => studioApi.convertArtifacts(body),
    onSuccess: async ({ outcomes }) => {
      await queryClient.invalidateQueries({ queryKey: ["studio"] });
      const converted = outcomes.filter((outcome) => outcome.status === "converted").length;
      const failed = outcomes.filter((outcome) => outcome.status === "failed").length;
      if (converted) toast.success(`${converted} ${converted === 1 ? "view" : "views"} now in BI`);
      if (failed) toast.error(`${failed} ${failed === 1 ? "view" : "views"} could not be converted`);
      onReport?.(outcomes);
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

/** The banner on a read-only view: what converting changes, and the action. */
export function LegacyBanner({
  artifactId,
  blockers,
  notes,
}: {
  artifactId: string;
  blockers: string[];
  notes: string[];
}) {
  const [confirming, setConfirming] = useState(false);
  const convert = useConvertArtifacts((outcomes) => {
    const kept = outcomes.find((outcome) => outcome.status === "kept");
    if (kept) toast.message("Kept read-only", { description: kept.blockers.join("; ") });
  });
  const run = (force: boolean) => convert.mutate({ artifact_ids: [artifactId], force });
  return (
    <section
      aria-label="Saved before BI"
      className="mb-4 flex flex-wrap items-start gap-3 rounded-lg border bg-surface-1 px-4 py-3"
    >
      <Archive aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-secondary" />
      <div className="min-w-0 flex-1 basis-64">
        <p className="type-ui font-medium">Saved before BI, read-only</p>
        <p className="type-meta mt-0.5">
          Convert it to edit, share and schedule it in BI. It keeps its place in Explore and in conversations.
        </p>
        <ChangeList blockers={blockers} notes={notes} />
      </div>
      <Button
        type="button"
        size="sm"
        variant={blockers.length ? "outline" : "default"}
        disabled={convert.isPending}
        onClick={() => (blockers.length ? setConfirming(true) : run(false))}
      >
        {convert.isPending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : null}
        {blockers.length ? "Convert anyway" : "Convert to BI"}
      </Button>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Convert without these?</DialogTitle>
            <DialogDescription>
              BI computes these differently, so the converted view leaves them out. Recreate them in BI afterwards if you
              need them.
            </DialogDescription>
          </DialogHeader>
          <ul className="type-ui list-disc space-y-1 pl-5">
            {blockers.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirming(false)}>
              Keep read-only
            </Button>
            <Button
              type="button"
              onClick={() => {
                setConfirming(false);
                run(true);
              }}
            >
              Convert anyway
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

/** The notice in Explore while any view is still read-only. */
export function LegacyNotice({ count }: { count: number }) {
  const [report, setReport] = useState<StudioArtifactConversion[] | null>(null);
  const convert = useConvertArtifacts(setReport);
  const kept = (report ?? []).filter((outcome) => outcome.status === "kept");
  return (
    <>
      <section
        aria-label="Views saved before BI"
        className="flex flex-wrap items-center gap-3 rounded-lg border bg-surface-1 px-4 py-3"
      >
        <Archive aria-hidden="true" className="size-4 shrink-0 text-ink-secondary" />
        <p className="type-ui min-w-0 flex-1 basis-64">
          {count} {count === 1 ? "view was" : "views were"} saved before BI and {count === 1 ? "is" : "are"} read-only.
          Convert them to edit in BI.
        </p>
        <Button type="button" size="sm" disabled={convert.isPending} onClick={() => convert.mutate({})}>
          {convert.isPending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : null}
          Convert all
        </Button>
      </section>
      <Dialog open={report !== null} onOpenChange={(open) => !open && setReport(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Conversion report</DialogTitle>
            <DialogDescription>
              {summary(report ?? [])}
            </DialogDescription>
          </DialogHeader>
          <ul className="flex flex-col divide-y rounded-lg border">
            {(report ?? []).map((outcome) => (
              <li key={outcome.artifact_id} className="px-3 py-2.5">
                <p className="type-ui flex items-baseline gap-2">
                  <span className="min-w-0 flex-1 truncate font-medium">{outcome.title}</span>
                  <span className="type-meta shrink-0">{STATUS[outcome.status]}</span>
                </p>
                {outcome.error ? <p className="type-meta mt-1 text-destructive">{outcome.error}</p> : null}
                <ChangeList blockers={outcome.blockers} notes={outcome.notes} />
              </li>
            ))}
          </ul>
          <DialogFooter>
            {kept.length ? (
              <Button
                type="button"
                variant="outline"
                disabled={convert.isPending}
                onClick={() => convert.mutate({ artifact_ids: kept.map((outcome) => outcome.artifact_id), force: true })}
              >
                Convert the kept {kept.length === 1 ? "view" : "views"} anyway
              </Button>
            ) : null}
            <Button type="button" onClick={() => setReport(null)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

const STATUS: Record<StudioArtifactConversion["status"], string> = {
  converted: "In BI",
  kept: "Kept read-only",
  failed: "Not converted",
};

function summary(outcomes: StudioArtifactConversion[]): string {
  const count = (status: StudioArtifactConversion["status"]) =>
    outcomes.filter((outcome) => outcome.status === status).length;
  const parts = [`${count("converted")} in BI`];
  if (count("kept")) parts.push(`${count("kept")} kept read-only because BI computes part of them differently`);
  if (count("failed")) parts.push(`${count("failed")} not converted`);
  return `${parts.join(", ")}.`;
}

function ChangeList({ blockers, notes }: { blockers: string[]; notes: string[] }) {
  if (!blockers.length && !notes.length) return null;
  return (
    <dl className="type-meta mt-2 grid gap-1">
      {blockers.length ? (
        <div>
          <dt className="inline font-medium text-foreground">Left out: </dt>
          <dd className="inline">{blockers.join("; ")}</dd>
        </div>
      ) : null}
      {notes.length ? (
        <div>
          <dt className="inline font-medium text-foreground">Looks different: </dt>
          <dd className="inline">{notes.join("; ")}</dd>
        </div>
      ) : null}
    </dl>
  );
}
