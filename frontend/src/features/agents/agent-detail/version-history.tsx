import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { agentVersionsApi, type Agent, type AgentCreateInput } from "../api";

const labels: Record<string, string> = {
  name: "Name",
  description: "Description",
  instructions_response: "Response instructions",
  instructions_orchestration: "Orchestration instructions",
  model_provider_id: "AI provider",
  model_name: "Model",
  default_tools: "Tools",
  default_skills: "Default skills",
  discoverable_skills: "Available skills",
  semantic_view_ids: "Semantic Views",
  resource_bindings: "Resource bindings and fixed filters",
  policy: "Tool approval policy",
  budget_seconds: "Time budget",
  budget_tokens: "Context budget",
  visibility: "Visibility",
};

function stable(value: unknown): string {
  if (value === undefined || value === null) return "Not set";
  if (typeof value === "string") return value || "Empty";
  return JSON.stringify(
    value,
    (_key, item) =>
      item && typeof item === "object" && !Array.isArray(item)
        ? Object.fromEntries(
            Object.entries(item).sort(([a], [b]) => a.localeCompare(b)),
          )
        : item,
    2,
  );
}

export function configurationDiff(current: Agent, saved: AgentCreateInput) {
  return Object.entries(saved)
    .filter(
      ([key, value]) => stable(current[key as keyof Agent]) !== stable(value),
    )
    .map(([key, value]) => ({
      key,
      label: labels[key] ?? key.replace(/_/g, " "),
      before: stable(current[key as keyof Agent]),
      after: stable(value),
    }));
}

export function AgentVersionHistory({
  agent,
  requestedVersion,
  hasUnsavedEdits = false,
  onClose,
}: {
  agent: Agent;
  requestedVersion?: string | null;
  hasUnsavedEdits?: boolean;
  onClose?: () => void;
}) {
  const client = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(false);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    if (requestedVersion) {
      setSelected(requestedVersion);
      setOpen(true);
      setOffset(0);
    }
  }, [requestedVersion]);
  const history = useQuery({
    queryKey: ["agent-versions", agent.agent_id, offset],
    queryFn: () => agentVersionsApi.list(agent.agent_id, offset),
    enabled: menuOpen || open,
  });
  const version = useQuery({
    queryKey: ["agent-version", agent.agent_id, selected],
    queryFn: () => agentVersionsApi.get(agent.agent_id, selected!),
    enabled: open && !!selected,
  });
  const changes = version.data
    ? configurationDiff(agent, version.data.configuration)
    : [];
  const publish = useMutation({
    mutationFn: () =>
      agentVersionsApi.publish(
        agent.agent_id,
        selected!,
        agent.config_revision ?? null,
      ),
    onSuccess: (updated) => {
      client.setQueryData(["agents", "detail", agent.agent_id], updated);
      client.invalidateQueries({ queryKey: ["agents"] });
      client.invalidateQueries({
        queryKey: ["agent-versions", agent.agent_id],
      });
      toast.success("Agent version published");
      changeOpen(false);
    },
  });
  const changeOpen = (value: boolean) => {
    setOpen(value);
    if (!value) {
      publish.reset();
      onClose?.();
    }
  };
  const inspect = (id: string) => {
    setSelected(id);
    setOpen(true);
    publish.reset();
  };
  return (
    <>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label="History"
            title="History"
            className="hover:bg-transparent dark:hover:bg-transparent"
          >
            <History aria-hidden className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="max-w-[calc(100vw-2rem)]">
          {history.isPending ? (
            <DropdownMenuItem disabled>Loading history…</DropdownMenuItem>
          ) : null}
          {history.isError ? (
            <DropdownMenuItem
              onSelect={(event) => {
                event.preventDefault();
                history.refetch();
              }}
            >
              Could not load history. Retry
            </DropdownMenuItem>
          ) : null}
          {history.data?.versions.slice(0, 5).map((item) => (
            <DropdownMenuItem
              key={item.version_id}
              onSelect={() => inspect(item.version_id)}
            >
              <span className="flex min-w-0 flex-col">
                <span className="truncate">{item.label}</span>
                <span className="text-xs">
                  {new Date(item.created_at).toLocaleString()}
                </span>
              </span>
            </DropdownMenuItem>
          ))}
          {history.data?.versions.length === 0 ? (
            <DropdownMenuItem disabled>No saved versions yet</DropdownMenuItem>
          ) : null}
          <DropdownMenuItem
            onSelect={() => {
              setOffset(0);
              setOpen(true);
            }}
          >
            View all history
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={open} onOpenChange={changeOpen}>
        <DialogContent className="inset-3 top-3 left-3 flex w-auto max-w-none translate-x-0 translate-y-0 flex-col gap-0 overflow-hidden p-0 sm:max-w-none">
          <DialogHeader className="shrink-0 border-b p-4 pr-12 text-start">
            <DialogTitle>Version history: {agent.name}</DialogTitle>
            <DialogDescription>
              Compare a saved configuration with the active agent. Publishing
              creates a new history entry.
            </DialogDescription>
          </DialogHeader>
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
            <aside className="flex shrink-0 flex-col border-b md:w-64 md:border-r md:border-b-0">
              <div className="flex min-h-0 flex-1 gap-1 overflow-auto p-2 md:flex-col">
                {history.isPending ? (
                  <p className="p-2 text-sm">Loading versions…</p>
                ) : null}
                {history.isError ? (
                  <Button variant="outline" onClick={() => history.refetch()}>
                    Retry history
                  </Button>
                ) : null}
                {history.data?.versions.length === 0 ? (
                  <p className="p-2 text-sm">
                    Save a configuration draft to start its history.
                  </p>
                ) : null}
                {history.data?.versions.map((item) => (
                  <Button
                    key={item.version_id}
                    variant={
                      selected === item.version_id ? "secondary" : "ghost"
                    }
                    className="h-auto min-h-11 shrink-0 justify-start whitespace-normal p-3 text-start"
                    aria-pressed={selected === item.version_id}
                    onClick={() => inspect(item.version_id)}
                  >
                    <span className="min-w-0">
                      <span className="block break-words">
                        {item.label}
                        {item.version_id === history.data.active_version_id
                          ? " (active)"
                          : ""}
                      </span>
                      <span className="block text-xs font-normal">
                        {new Date(item.created_at).toLocaleString()}
                      </span>
                    </span>
                  </Button>
                ))}
              </div>
              <div className="flex shrink-0 justify-between gap-2 border-t p-2">
                <Button
                  className="min-h-11"
                  variant="ghost"
                  disabled={offset === 0}
                  onClick={() => setOffset(offset - 30)}
                >
                  Newer
                </Button>
                <Button
                  className="min-h-11"
                  variant="ghost"
                  disabled={!history.data?.has_more}
                  onClick={() => setOffset(offset + 30)}
                >
                  Older
                </Button>
              </div>
            </aside>
            <div className="min-h-0 min-w-0 flex-1 overflow-y-auto p-4">
              {!selected ? (
                <p>Select a version to see its changes.</p>
              ) : version.isPending ? (
                <p role="status">Loading comparison…</p>
              ) : version.isError ? (
                <div role="alert">
                  <p>Could not load this version.</p>
                  <Button onClick={() => version.refetch()}>
                    Retry comparison
                  </Button>
                </div>
              ) : (
                <>
                  <p className="mb-4 text-sm">
                    {changes.length
                      ? `${changes.length} changed field${changes.length === 1 ? "" : "s"}`
                      : "This configuration matches the active agent."}
                  </p>
                  {changes.map((change) => (
                    <section key={change.key} className="mb-5 min-w-0">
                      <h3 className="mb-2 text-sm font-semibold">
                        {change.label}
                      </h3>
                      <div className="grid min-w-0 gap-2 md:grid-cols-2">
                        <div className="min-w-0 rounded-md border p-3">
                          <p className="mb-2 text-xs font-medium">Active</p>
                          <pre className="whitespace-pre-wrap break-words text-xs">
                            {change.before}
                          </pre>
                        </div>
                        <div className="min-w-0 rounded-md border border-primary/40 bg-accent p-3">
                          <p className="mb-2 text-xs font-medium">
                            Selected version
                          </p>
                          <pre className="whitespace-pre-wrap break-words text-xs">
                            {change.after}
                          </pre>
                        </div>
                      </div>
                    </section>
                  ))}
                </>
              )}
            </div>
          </div>
          <div className="shrink-0 space-y-2 border-t p-4">
            <p className="text-xs text-muted-foreground">
              History stores agent settings and resource references. Shared
              resource contents and global Decision settings keep their current
              values.
            </p>
            {hasUnsavedEdits ? (
              <p role="status" className="text-sm">
                Save a draft or discard your unsaved edits before publishing.
              </p>
            ) : null}
            {publish.isError ? (
              <div role="alert" className="text-sm">
                <p>{publish.error.message}</p>
                <Button
                  variant="outline"
                  onClick={() => {
                    client.invalidateQueries({
                      queryKey: ["agents", "detail", agent.agent_id],
                    });
                    publish.reset();
                  }}
                >
                  Reload active configuration
                </Button>
              </div>
            ) : null}
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                variant="outline"
                className="min-h-11"
                onClick={() => changeOpen(false)}
              >
                Close
              </Button>
              <Button
                className="min-h-11"
                disabled={
                  hasUnsavedEdits ||
                  !selected ||
                  !version.data ||
                  version.isError ||
                  publish.isPending ||
                  changes.length === 0
                }
                onClick={() => publish.mutate()}
              >
                {publish.isPending ? "Publishing…" : "Publish selected version"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
