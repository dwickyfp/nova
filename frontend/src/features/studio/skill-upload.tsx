import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, FileText, Upload } from "lucide-react";
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
import { skillsApi } from "@/features/agents/api";
import { MAX_SKILL_BYTES } from "./skill-document";

export function SkillUpload({ onClose }: { onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<{ name: string; document: string } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const queryClient = useQueryClient();
  const verify = useMutation({
    mutationFn: (document: string) => skillsApi.verify(document),
  });
  const save = useMutation({
    mutationFn: () => skillsApi.upload(file!.document),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["studio", "skills"] }),
        queryClient.invalidateQueries({ queryKey: ["studio", "capabilities"] }),
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
      ]);
      toast.success("Skill saved");
      onClose();
    },
  });
  const busy = reading || verify.isPending || save.isPending;
  async function select(files: FileList | null) {
    if (busy || !files?.length) return;
    setFile(null);
    setError(null);
    verify.reset();
    save.reset();
    const selected = files[0];
    if (files.length !== 1 || selected.name.toLowerCase() !== "skill.md") {
      setError("Choose one file named SKILL.md.");
      return;
    }
    if (selected.size > MAX_SKILL_BYTES) {
      setError("SKILL.md must be 25 MB or smaller.");
      return;
    }
    setReading(true);
    try {
      const document = new TextDecoder("utf-8", { fatal: true }).decode(
        await selected.arrayBuffer(),
      );
      if (!document.trim()) throw new Error("empty");
      setFile({ name: selected.name, document });
    } catch {
      setError("Choose a nonempty UTF-8 Markdown file.");
    } finally {
      setReading(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent className="flex max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="px-6 pt-7 sm:px-8">
          <DialogTitle>
            {verify.data ? "Verify your skill" : "Upload a skill"}
          </DialogTitle>
          <DialogDescription className="sr-only">
            Upload a SKILL.md file, verify its format, then save it to your
            private skills.
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6 sm:px-8">
          {verify.data ? (
            <div className="space-y-5">
              <p
                role="status"
                className="flex items-center gap-2 text-sm font-medium"
              >
                <Check className="size-4" aria-hidden="true" />
                Verification complete
              </p>
              <dl className="space-y-3 text-sm">
                <div>
                  <dt className="text-muted-foreground">Name</dt>
                  <dd className="mt-1 break-words font-medium">
                    {verify.data.name}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Description</dt>
                  <dd className="mt-1 break-words">
                    {verify.data.description}
                  </dd>
                </div>
              </dl>
              <details className="rounded-md border p-3 text-sm">
                <summary className="cursor-pointer focus-visible:outline-ring">
                  Review SKILL.md
                </summary>
                <pre className="mt-3 whitespace-pre-wrap break-words font-mono text-xs">
                  {file?.document}
                </pre>
              </details>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Format, required fields, and name availability checked. This
                does not run the skill or verify the accuracy of its
                instructions. Only you can access it after saving.
              </p>
            </div>
          ) : (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => input.current?.click()}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                void select(event.dataTransfer.files);
              }}
              className="h-auto w-full flex-col gap-3 whitespace-normal border-dashed bg-transparent px-5 py-16 text-center shadow-none"
            >
              {file ? (
                <FileText
                  className="size-5 text-muted-foreground"
                  aria-hidden="true"
                />
              ) : (
                <Upload
                  className="size-5 text-muted-foreground"
                  aria-hidden="true"
                />
              )}
              <span>
                {reading
                  ? "Reading file…"
                  : file
                    ? file.name
                    : "Click to upload or drop SKILL.md"}
              </span>
              <span className="font-normal text-muted-foreground">
                {file
                  ? "Click to choose a different file."
                  : "Upload a Markdown file with your skill instructions."}
              </span>
              <span className="font-normal text-muted-foreground">
                Maximum size: 25 MB.
              </span>
            </Button>
          )}
          <input
            ref={input}
            type="file"
            accept=".md,text/markdown,text/plain"
            aria-label="Upload SKILL.md"
            className="sr-only"
            tabIndex={-1}
            disabled={busy}
            onChange={(event) => {
              void select(event.target.files);
              event.target.value = "";
            }}
          />
          {error || verify.error || save.error ? (
            <p role="alert" className="mt-4 text-sm text-destructive">
              {error || verify.error?.message || save.error?.message}
            </p>
          ) : null}
        </div>
        <DialogFooter className="border-t px-6 py-4 sm:px-8">
          {verify.data ? (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                verify.reset();
                save.reset();
              }}
            >
              Back
            </Button>
          ) : null}
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!file || busy}
            onClick={() =>
              verify.data ? save.mutate() : verify.mutate(file!.document)
            }
          >
            {save.isPending
              ? "Saving skill…"
              : verify.isPending
                ? "Verifying…"
                : verify.data
                  ? "Complete"
                  : "Verify skill"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
