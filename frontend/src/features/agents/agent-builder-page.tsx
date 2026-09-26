import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { ArrowUpRight, MessageSquarePlus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/layout/header";
import { Main } from "@/components/layout/main";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { agentsApi } from "./api";
import { AgentOverviewTab } from "./agent-detail/overview-tab";
import { AgentConfigurationTab } from "./agent-detail/configuration-tab";
import { AgentAccessTab } from "./agent-detail/access-tab";
import { AgentObservabilityTab } from "./agent-detail/observability-tab";
import { AgentVersionHistory } from "./agent-detail/version-history";

/**
 * Agent detail — the four-tab surface: Overview, Configuration, Access, and
 * Observability. The header is shared; each tab owns its own data and layout.
 */
export function AgentBuilderPage() {
  const { agentId } = useParams({ from: "/_authenticated/agents/$agentId" });
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState("overview");
  const [draftVersion, setDraftVersion] = useState<string | null>(null);
  const [hasUnsavedEdits, setHasUnsavedEdits] = useState(false);

  const agentQuery = useQuery({
    queryKey: ["agents", "detail", agentId],
    queryFn: () => agentsApi.get(agentId),
  });

  const remove = useMutation({
    mutationFn: () => agentsApi.remove(agentId),
    onSuccess: () => {
      toast.success("Agent deleted");
      queryClient.invalidateQueries({ queryKey: ["agents", "list"] });
      navigate({ to: "/agents" });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const openStudio = () => {
    window.open(
      `/studio?agent=${encodeURIComponent(agentId)}`,
      "_blank",
      "noopener",
    );
  };

  if (agentQuery.isError) {
    return <><Header fixed /><Main scroll><h1 className="text-xl">Agent</h1><p role="alert">Could not load this agent.</p>
      <Button onClick={() => agentQuery.refetch()}>Retry</Button></Main></>;
  }
  if (agentQuery.isLoading || !agentQuery.data) {
    return (
      <>
        <Header fixed />
        <Main scroll>
          <Skeleton className="h-96 w-full" />
        </Main>
      </>
    );
  }

  const agent = agentQuery.data;

  return (
    <>
      <Header fixed />
      <Main fixed>
        <div className="mb-4 flex shrink-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-2xl leading-8 font-normal">
                {agent.name}
              </h1>
              <Badge variant="outline">Active</Badge>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {(agent.semantic_view_ids ?? []).length > 0
                ? `${agent.semantic_view_ids?.length} Semantic View${
                    agent.semantic_view_ids?.length === 1 ? "" : "s"
                  } · `
                : "No Semantic View · "}
              Updated {new Date(agent.updated_at).toLocaleString()}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <AgentVersionHistory agent={agent} requestedVersion={draftVersion} hasUnsavedEdits={hasUnsavedEdits} onClose={() => setDraftVersion(null)} />
            <Button variant="outline" onClick={openStudio}>
              <MessageSquarePlus className="size-4" />
              Open in Nova Studio
              <ArrowUpRight className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => remove.mutate()}
              aria-label="Delete agent"
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        </div>

        <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1 overflow-y-auto">
          <TabsList className="h-auto flex-wrap">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="configuration">Configuration</TabsTrigger>
            <TabsTrigger value="access">Access</TabsTrigger>
            <TabsTrigger value="observability">Observability</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="mt-6">
            <AgentOverviewTab agentId={agentId} />
          </TabsContent>
          <TabsContent value="configuration" forceMount className="mt-6 data-[state=inactive]:hidden">
            <AgentConfigurationTab
              agent={agent}
              onDirtyChange={setHasUnsavedEdits}
              onDraftSaved={(id) => { queryClient.invalidateQueries({ queryKey: ["agent-versions", agentId] }); setDraftVersion(id); }}
              onSaved={() =>
                queryClient.invalidateQueries({
                  queryKey: ["agents", "detail", agentId],
                })
              }
            />
          </TabsContent>
          <TabsContent value="access" className="mt-6">
            <AgentAccessTab agentId={agentId} agentName={agent.name}
              hasSemanticViews={(agent.semantic_view_ids ?? []).length > 0} />
          </TabsContent>
          <TabsContent value="observability" className="mt-6">
            <AgentObservabilityTab agentId={agentId} />
          </TabsContent>
        </Tabs>
      </Main>
    </>
  );
}
