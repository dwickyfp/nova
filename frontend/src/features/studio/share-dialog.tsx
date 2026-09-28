import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { sharingApi, type Share } from "@/features/agents/studio-intelligence-api";

/**
 * Share a conversation or dashboard with a user or a role.
 *
 * The recipient sees the text and the questions behind each result, never your
 * rows: every result re-runs with their own access.
 */
export function ShareDialog({
  objectType,
  objectId,
  title,
  onClose,
}: {
  objectType: Share["object_type"];
  objectId: string | null;
  title: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [targetType, setTargetType] = useState<"user" | "role">("user");
  const [name, setName] = useState("");
  const key = ["studio", "shares", objectType, objectId];
  const shares = useQuery({
    queryKey: key,
    queryFn: () => sharingApi.list(objectType, objectId as string),
    enabled: Boolean(objectId),
  });
  const create = useMutation({
    mutationFn: () =>
      sharingApi.create(objectType, objectId as string, {
        target_type: targetType, target_name: name.trim(),
      }),
    onSuccess: () => {
      toast.success(`Shared with ${name.trim()}`);
      setName("");
      queryClient.invalidateQueries({ queryKey: key });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const revoke = useMutation({
    mutationFn: (shareId: string) => sharingApi.revoke(shareId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
    onError: (error: Error) => toast.error(error.message),
  });
  const valid = /^[A-Za-z_][\w.-]*$/.test(name.trim());
  return (
    <Dialog open={Boolean(objectId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Share “{title}”</DialogTitle>
          <DialogDescription>
            {objectType === "dashboard"
              ? "They see this layout, and every tile runs with their own access. Your results are not included."
              : "They see the conversation and can re-run each result with their own access. Your results are not included."}
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid && !create.isPending) create.mutate();
          }}
        >
          <div className="space-y-1.5 sm:w-28">
            <Label htmlFor="share-type">With</Label>
            <Select value={targetType} onValueChange={(value) => setTargetType(value as "user" | "role")}>
              <SelectTrigger id="share-type"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="user">User</SelectItem>
                <SelectItem value="role">Role</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="min-w-0 flex-1 space-y-1.5">
            <Label htmlFor="share-name">{targetType === "user" ? "Username" : "Role name"}</Label>
            <Input id="share-name" value={name} onChange={(e) => setName(e.target.value)}
              placeholder={targetType === "user" ? "analyst_1" : "SALES_ANALYST"} />
          </div>
          <Button type="submit" disabled={!valid || create.isPending}>Share</Button>
        </form>
        <div className="space-y-2">
          <p className="text-sm font-medium">Shared with</p>
          {(shares.data?.shares ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">Only you.</p>
          ) : (
            <ul className="divide-y rounded-md border">
              {shares.data?.shares.map((share) => (
                <li key={share.share_id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                  <span className="min-w-0 truncate">
                    {share.target_name}
                    <span className="text-muted-foreground"> · {share.target_type}</span>
                  </span>
                  <Button variant="ghost" size="icon" aria-label={`Stop sharing with ${share.target_name}`}
                    disabled={revoke.isPending} onClick={() => revoke.mutate(share.share_id)}>
                    <X className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
