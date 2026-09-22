import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  agentAccessApi,
  rolesApi,
  type AccessCheckResult,
} from "@/features/agents/api";

/**
 * Agent Access — which roles may use the agent, and Verify Access.
 *
 * Verify Access resolves what the agent actually depends on (its custom function
 * tools, the tables behind its semantic model, its database) and checks each
 * against the role's engine grants. A red row is a real gap the engine would
 * refuse at run time.
 */
export function AgentAccessTab({ agentId }: { agentId: string }) {
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const [roleName, setRoleName] = useState("");
  const [grantType, setGrantType] = useState<"USAGE" | "OWNERSHIP">("USAGE");
  const [result, setResult] = useState<AccessCheckResult | null>(null);

  const rolesQuery = useQuery({
    queryKey: ["agents", "access", agentId],
    queryFn: () => agentAccessApi.list(agentId),
  });
  // Every role on the engine, for the picker. The free-text field is gone: a
  // typo would silently grant nothing, so the role is chosen from what exists.
  const availableRolesQuery = useQuery({
    queryKey: ["agents", "roles"],
    queryFn: () => rolesApi.list(),
    enabled: addOpen,
  });

  const addRole = useMutation({
    mutationFn: () => agentAccessApi.add(agentId, roleName.trim(), grantType),
    onSuccess: () => {
      toast.success(`Role ${roleName} added`);
      setAddOpen(false);
      setRoleName("");
      setGrantType("USAGE");
      queryClient.invalidateQueries({
        queryKey: ["agents", "access", agentId],
      });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const removeRole = useMutation({
    mutationFn: (role: string) => agentAccessApi.remove(agentId, role),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agents", "access", agentId],
      });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const verify = useMutation({
    mutationFn: (role: string) => agentAccessApi.verify(agentId, role),
    onSuccess: (data) => setResult(data),
    onError: (e: Error) => toast.error(e.message),
  });

  const onVerify = (role: string) => {
    verify.mutate(role);
  };

  const roles = rolesQuery.data?.roles ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium">Access</h2>
          <p className="text-sm text-muted-foreground">
            Roles that can see and use this agent.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={() => setAddOpen(true)}>
            <Plus className="size-4" />
            Add role
          </Button>
        </div>
      </div>

      {rolesQuery.isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : roles.length === 0 ? (
        <div className="rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">
          No roles yet. Add a role to grant access to this agent.
        </div>
      ) : (
        <div className="divide-y rounded-2xl border">
          {roles.map((role) => (
            <div
              key={role.role_name}
              className="flex items-center justify-between gap-3 px-4 py-3"
            >
              <div className="flex items-center gap-3">
                <span className="text-sm font-medium">{role.role_name}</span>
                <Badge variant="outline">{role.grant_type}</Badge>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onVerify(role.role_name)}
                >
                  Verify
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => removeRole.mutate(role.role_name)}
                  aria-label={`Remove ${role.role_name}`}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {result ? (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t bg-card p-4 shadow-lg">
          <div className="mx-auto flex max-w-4xl items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="mb-2 flex items-center gap-2">
                <ShieldCheck className="size-4" />
                <span className="font-medium">
                  Verify access: {result.role_name}
                </span>
                <Badge
                  variant={result.all_granted ? "secondary" : "destructive"}
                >
                  {result.all_granted ? "All granted" : "Gaps found"}
                </Badge>
              </div>
              <div className="space-y-1">
                {result.items.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    This agent has no external dependencies to check.
                  </p>
                ) : (
                  result.items.map((item, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      {item.granted ? (
                        <Check className="size-3.5 text-success" />
                      ) : (
                        <X className="size-3.5 text-destructive" />
                      )}
                      <Badge
                        variant="outline"
                        className="font-mono text-[10px]"
                      >
                        {item.kind}
                      </Badge>
                      <span className="font-mono text-xs">{item.name}</span>
                      <span className="truncate text-xs text-muted-foreground">
                        {item.detail}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>
            <Button variant="ghost" size="icon" onClick={() => setResult(null)}>
              <X className="size-4" />
            </Button>
          </div>
        </div>
      ) : null}

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add role</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Role</Label>
              <Select value={roleName || undefined} onValueChange={setRoleName}>
                <SelectTrigger>
                  <SelectValue
                    placeholder={
                      availableRolesQuery.isLoading
                        ? "Loading roles…"
                        : "Select a role"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {(availableRolesQuery.data?.roles ?? []).map((role) => (
                    <SelectItem key={role} value={role} className="font-mono">
                      {role}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Grant type</Label>
              <Select
                value={grantType}
                onValueChange={(v) => setGrantType(v as typeof grantType)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="USAGE">USAGE</SelectItem>
                  <SelectItem value="OWNERSHIP">OWNERSHIP</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!roleName.trim() || addRole.isPending}
              onClick={() => addRole.mutate()}
            >
              Add role
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
