import { useId, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { Label } from "@/components/ui/label";
import { useAuthStore } from "@/stores/auth-store";
import type { AutoRun } from "@/features/agents/api";
import { workflowResourcesApi } from "./workflow-api";
import { latestParticipants } from "./smart-agent-tree";

export function ResourcePanel({ threadId, runs }: { threadId: string; runs: AutoRun[] }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const root = runs.find((run) => run.depth === 0);
  if (!root) return null;
  return <Resources key={`${epoch}:${root.run_id}`} threadId={threadId} root={root} runs={runs} epoch={epoch} />;
}

function Resources({ threadId, root, runs, epoch }: { threadId: string; root: AutoRun; runs: AutoRun[]; epoch: number }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const resources = useQuery({ queryKey: ["studio", "resources", epoch, threadId, root.run_id], queryFn: ({ signal }) => workflowResourcesApi.list(threadId, root.run_id, "/root", signal), enabled: open, retry: false, staleTime: 0, gcTime: 0 });
  const children = latestParticipants(runs).filter((run) => run.depth === 1 && run.agent_path);
  const grant = useMutation({ mutationFn: () => workflowResourcesApi.grant(threadId, { root_run_id: root.run_id, grantor: "/root", target, resource_refs: selected }) });
  return <section aria-label="Attachment access" className="space-y-3">
    <Button variant="ghost" className="min-h-11 w-full justify-start" aria-expanded={open} onClick={() => setOpen((value) => !value)}>Manage attachment access</Button>
    {open && <div className="space-y-3 rounded-md border p-3">
      <p className="text-xs text-muted-foreground">Select files to share with a specialist. Each specialist has its own attachment access.</p>
      {resources.isPending ? <LoadingLines rows={1} /> : resources.isError ? <div role="alert" className="space-y-2"><p className="text-xs text-destructive">{resources.error.message}</p><Button variant="outline" className="min-h-11" onClick={() => void resources.refetch()}>Reload files</Button></div> : resources.data?.resources.length ? <>
        <fieldset className="min-w-0 space-y-2"><legend className="mb-2 text-xs font-medium">Files available to Smart</legend>
          {resources.data.resources.map((resource) => <label key={resource.resource_id} className="flex min-h-11 min-w-0 items-center gap-2 text-sm">
            <input type="checkbox" checked={selected.includes(resource.resource_id)} onChange={(event) => { grant.reset(); setSelected((current) => event.target.checked ? [...current, resource.resource_id] : current.filter((value) => value !== resource.resource_id)); }} />
            <span className="min-w-0 break-words">{resource.name}</span>
          </label>)}
        </fieldset>
        <div className="space-y-2"><Label htmlFor={id}>Specialist</Label><select id={id} className="min-h-11 w-full min-w-0 rounded-md border bg-card px-2 text-sm" value={target} onChange={(event) => { grant.reset(); setTarget(event.target.value); }}>
          <option value="">Select a specialist</option>{children.map((child) => <option key={child.run_id} value={child.agent_path}>{child.agent_name || child.objective || child.agent_path}</option>)}
        </select></div>
        {!children.length && <p className="text-xs text-muted-foreground">Smart has not delegated to a specialist in this run.</p>}
        <Button variant="outline" className="min-h-11" disabled={!target || !selected.length || grant.isPending} onClick={() => grant.mutate()}>{grant.isPending ? "Granting access…" : "Grant selected files"}</Button>
        {grant.isSuccess && <p role="status" className="text-xs">Selected files are available to {grant.data.participant}.</p>}
        {grant.isError && <p role="alert" className="text-xs text-destructive">{grant.error.message}</p>}
      </> : <p className="text-xs text-muted-foreground">This run has no attachments available to delegate.</p>}
    </div>}
  </section>;
}
