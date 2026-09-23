import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { skillsApi } from "@/features/agents/api";
import { MAX_SKILL_BYTES } from "./skill-document";

export function SkillEditor({
  initialDocument,
  skillId,
  onClose,
}: {
  initialDocument: string;
  skillId?: string;
  onClose: () => void;
}) {
  const [document, setDocument] = useState(initialDocument);
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () =>
      skillId
        ? skillsApi.update(skillId, document)
        : skillsApi.upload(document),
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
  const tooLarge = new TextEncoder().encode(document).length > MAX_SKILL_BYTES;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !save.isPending) onClose();
      }}
    >
      <DialogContent className="flex max-h-[90dvh] flex-col sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {skillId ? "Edit skill" : "Review your skill"}
          </DialogTitle>
          <DialogDescription>
            Only you can access this skill. Your agents can use it after you
            save.
          </DialogDescription>
        </DialogHeader>
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pe-3">
          <Label htmlFor="skill-document">SKILL.md</Label>
          <Textarea
            id="skill-document"
            value={document}
            onChange={(event) => {
              setDocument(event.target.value);
              save.reset();
            }}
            rows={16}
            spellCheck={false}
            className="min-h-0 flex-1 font-mono text-sm"
            disabled={save.isPending}
          />
          <p className="text-xs text-muted-foreground">
            Include a name and description in YAML frontmatter, followed by the
            instructions. Maximum 25 MB.
          </p>
          {tooLarge || save.error ? (
            <p role="alert" className="text-sm text-destructive">
              {tooLarge
                ? "SKILL.md must be 25 MB or smaller."
                : save.error?.message}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={!document.trim() || tooLarge || save.isPending}
          >
            {save.isPending ? "Saving skill…" : "Save skill"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
