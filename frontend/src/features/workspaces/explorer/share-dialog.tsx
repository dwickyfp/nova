import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, X } from "lucide-react";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { workspaceApi, type WorkspaceShare, type WorkspaceSpace } from "./api";

const PERMISSION_LABEL: Record<WorkspaceShare["permission"], string> = {
  view: "Can view",
  edit: "Can edit",
};

/**
 * Share a workspace with roles. Everyone who has one of these roles assigned
 * sees the workspace. Sharing shares files only: queries and notebooks always
 * run as the person who runs them, under their own role.
 */
export function ShareDialog({
  space,
  onOpenChange,
}: {
  space: WorkspaceSpace | null;
  onOpenChange: (open: boolean) => void;
}) {
  const open = space !== null;
  const sharesQuery = useQuery({
    queryKey: ["workspace-shares", space?.id],
    queryFn: () => workspaceApi.shares(space!.id),
    enabled: open,
  });
  const rolesQuery = useQuery({
    queryKey: ["workspace-share-roles"],
    queryFn: workspaceApi.roles,
    enabled: open,
    staleTime: 60_000,
  });
  const [shares, setShares] = useState<WorkspaceShare[]>([]);
  const [adding, setAdding] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (sharesQuery.data) setShares(sharesQuery.data.shares);
  }, [sharesQuery.data]);
  useEffect(() => {
    if (!open) setAdding("");
  }, [open]);

  const available = useMemo(
    () => (rolesQuery.data?.roles ?? []).filter((role) => !shares.some((s) => s.role === role)),
    [rolesQuery.data, shares],
  );

  const save = async () => {
    if (!space) return;
    setSaving(true);
    try {
      await workspaceApi.setShares(space.id, shares);
      toast.success(
        shares.length
          ? `${space.name} is shared with ${shares.length} ${shares.length === 1 ? "role" : "roles"}`
          : `${space.name} is no longer shared`,
      );
      onOpenChange(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The sharing could not be saved");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Share {space?.name}</DialogTitle>
          <DialogDescription>
            Everyone with one of these roles assigned sees this workspace. Its files are shared;
            queries and notebooks still run as each person, under their own role.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3 py-1">
          <div className="flex gap-2">
            <Select value={adding} onValueChange={setAdding}>
              <SelectTrigger className="flex-1" aria-label="Role to share with">
                <SelectValue
                  placeholder={rolesQuery.isLoading ? "Loading roles" : "Choose a role"}
                />
              </SelectTrigger>
              <SelectContent>
                {available.map((role) => (
                  <SelectItem key={role} value={role}>
                    {role}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              disabled={!adding}
              onClick={() => {
                setShares((current) => [...current, { role: adding, permission: "view" }]);
                setAdding("");
              }}
            >
              Add
            </Button>
          </div>

          {sharesQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading</p>
          ) : shares.length === 0 ? (
            <p className="text-sm text-muted-foreground">Only you can see this workspace</p>
          ) : (
            <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
              {shares.map((share) => (
                <li key={share.role} className="flex items-center gap-2 px-3 py-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-sm">{share.role}</span>
                  <Select
                    value={share.permission}
                    onValueChange={(permission) =>
                      setShares((current) =>
                        current.map((item) =>
                          item.role === share.role
                            ? { ...item, permission: permission as WorkspaceShare["permission"] }
                            : item,
                        ),
                      )
                    }
                  >
                    <SelectTrigger className="h-8 w-32" aria-label={`Permission for ${share.role}`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {(["view", "edit"] as const).map((permission) => (
                        <SelectItem key={permission} value={permission}>
                          {PERMISSION_LABEL[permission]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-8 text-muted-foreground"
                    aria-label={`Stop sharing with ${share.role}`}
                    onClick={() =>
                      setShares((current) => current.filter((item) => item.role !== share.role))
                    }
                  >
                    <X className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => void save()} disabled={saving || sharesQuery.isLoading}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : null}
            Save sharing
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
