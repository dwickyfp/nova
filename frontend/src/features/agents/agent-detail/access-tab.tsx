import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Brain,
  CheckCircle2,
  Plus,
  Trash2,
  X,
} from "lucide-react";
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
import { useAssistant } from "@/features/assistant/assistant-provider";
import { buildAccessGrantPrompt } from "./access-prompt";

const requiredPrivilege: Record<string, string> = {
  table: "SELECT",
  database: "USAGE",
  function: "EXECUTE",
};

/**
 * Agent Access — which roles may use the agent, and Verify Access.
 *
 * Verify Access resolves what the agent actually depends on (its custom function
 * tools, the tables behind its Semantic Views, its database) and checks each
 * against the role's engine grants. A red row is a real gap the engine would
 * refuse at run time.
 */
export function AgentAccessTab({
  agentId,
  agentName,
  hasSemanticViews = false,
}: {
  agentId: string;
  agentName?: string;
  hasSemanticViews?: boolean;
}) {
  const queryClient = useQueryClient();
  const { newChatAndSend } = useAssistant();
  const [addOpen, setAddOpen] = useState(false);
  const [roleName, setRoleName] = useState("");
  const [grantType, setGrantType] = useState<"USAGE" | "OWNERSHIP">("USAGE");
  const [result, setResult] = useState<AccessCheckResult | null>(null);
  const [verifyError, setVerifyError] = useState<{
    roleName: string;
    message: string;
  } | null>(null);

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
      queryClient.invalidateQueries({ queryKey: ["studio", "agents"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const removeRole = useMutation({
    mutationFn: (role: string) => agentAccessApi.remove(agentId, role),
    onSuccess: (_, role) => {
      if (result?.role_name === role) setResult(null);
      if (verifyError?.roleName === role) setVerifyError(null);
      queryClient.invalidateQueries({
        queryKey: ["agents", "access", agentId],
      });
      queryClient.invalidateQueries({ queryKey: ["studio", "agents"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const verify = useMutation({
    mutationFn: (role: string) => agentAccessApi.verify(agentId, role),
    onMutate: () => {
      setResult(null);
      setVerifyError(null);
    },
    onSuccess: (data) => {
      setResult(data);
      queryClient.invalidateQueries({
        queryKey: ["agents", "access", agentId],
      });
      queryClient.invalidateQueries({ queryKey: ["studio", "agents"] });
    },
    onError: (e: Error, role) =>
      setVerifyError({ roleName: role, message: e.message }),
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

      {hasSemanticViews && roles.some((role) => !role.verified) ? (
        <p role="status" className="rounded-md border border-warning bg-warning/10 px-4 py-3 text-sm">
          A bound Semantic View changed or this role has not been checked. Verify access again for each role before the agent runs.
        </p>
      ) : null}

      {rolesQuery.isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : roles.length === 0 ? (
        <div className="rounded-2xl border border-dashed p-8 text-center text-sm text-muted-foreground">
          No roles yet. Add a role to grant access to this agent.
        </div>
      ) : (
        <div className="divide-y rounded-2xl border">
          {roles.map((role) => {
            const roleResult =
              result?.role_name === role.role_name ? result : null;
            const roleError =
              verifyError?.roleName === role.role_name ? verifyError : null;
            const gaps =
              roleResult?.items.filter((item) => !item.granted) ?? [];
            return (
              <div key={role.role_name}>
                <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">
                      {role.role_name}
                    </span>
                    <Badge variant="outline">{role.grant_type}</Badge>
                    <Badge variant={role.verified ? "secondary" : "outline"}>
                      {role.verified ? "Verified" : "Needs verification"}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={verify.isPending}
                      onClick={() => onVerify(role.role_name)}
                    >
                      {verify.isPending && verify.variables === role.role_name
                        ? "Checking…"
                        : hasSemanticViews && !role.verified ? "Verify access again" : "Verify"}
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
                {roleResult || roleError ? (
                  <div
                    className={`border-t px-4 py-4 sm:px-5 ${
                      roleError || gaps.length > 0
                        ? "bg-destructive/5"
                        : "bg-success/5"
                    }`}
                    role={roleError || gaps.length > 0 ? "alert" : "status"}
                  >
                    <div className="flex items-start gap-3">
                      {roleError || gaps.length > 0 ? (
                        <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
                      ) : (
                        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
                      )}
                      <div className="min-w-0 flex-1 space-y-2">
                        <div>
                          <p className="text-sm font-medium">
                            {roleError
                              ? "Verification could not finish"
                              : gaps.length > 0
                                ? `${gaps.length} access ${gaps.length === 1 ? "gap" : "gaps"} found`
                                : "Access verified"}
                          </p>
                          <p className="mt-0.5 text-sm text-muted-foreground">
                            {roleError
                              ? roleError.message
                              : gaps.length > 0
                                ? "Grant these permissions in Access Control, then verify again."
                                : roleResult?.items.length
                                  ? `All ${roleResult.items.length} resources are accessible to this role.`
                                  : "This agent has no external resources to check."}
                          </p>
                        </div>
                        {gaps.length > 0 ? (
                          <div className="space-y-2">
                            <ul className="grid gap-1.5 sm:grid-cols-2">
                              {gaps.map((item) => (
                                <li
                                  key={`${item.kind}:${item.name}`}
                                  className="flex min-w-0 items-start gap-2 rounded-md border bg-background/70 px-3 py-2 text-xs"
                                >
                                  <span className="shrink-0 text-muted-foreground">
                                    {requiredPrivilege[item.kind] ?? item.kind}
                                  </span>
                                  <span className="min-w-0 break-all font-mono">
                                    {item.name}
                                  </span>
                                </li>
                              ))}
                            </ul>
                            <a
                              href="/access-control"
                              className="inline-block text-sm font-medium text-primary underline-offset-4 hover:underline"
                            >
                              Open Access Control
                            </a>
                          </div>
                        ) : null}
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        {gaps.length > 0 ? (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-11 sm:size-8"
                            aria-label="Ask Nove to grant missing access"
                            title="Ask Nove to grant missing access"
                            onClick={() =>
                              newChatAndSend(
                                buildAccessGrantPrompt({
                                  agentName: agentName || agentId,
                                  roleName: role.role_name,
                                  gaps,
                                }),
                              )
                            }
                          >
                            <Brain aria-hidden="true" className="size-4" />
                          </Button>
                        ) : null}
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-11 sm:size-8"
                          onClick={() => {
                            setResult(null);
                            setVerifyError(null);
                          }}
                          aria-label="Dismiss verification result"
                        >
                          <X className="size-4" />
                        </Button>
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

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
