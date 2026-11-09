import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { ArrowUpRight, MessageSquarePlus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/layout/header";
import { Main } from "@/components/layout/main";
import { StateBadge } from "@/components/ui/status-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { semanticViewsApi } from "@/features/intelligence/semantic-views-api";
import { agentAccessApi, agentsApi, observabilityApi, toolsApi } from "./api";
import { automationsApi } from "./studio-intelligence-api";
import { AgentOverviewTab } from "./agent-detail/overview-tab";
import { AgentConfigurationTab } from "./agent-detail/configuration-tab";
import { AgentAccessTab } from "./agent-detail/access-tab";
import { AgentObservabilityTab } from "./agent-detail/observability-tab";
import { AgentVersionHistory } from "./agent-detail/version-history";
import { AgentImproveTab } from "./agent-detail/improve-tab";
import { AgentAutomationsTab } from "./agent-detail/automations-tab";

/** A tab's own scroll region below the fixed tab row. */
const SCROLL = "mt-6 min-h-0 flex-1 overflow-y-auto pb-6";

/**
 * Agent detail: Overview, Configuration, Access, Observability, Improve, and
 * Automations. The header is shared; each tab owns its own data and layout.
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

  // Warm every tab's data as soon as the page opens, so switching tabs shows
  // content instead of a loading state. The tabs read the same query keys.
  useEffect(() => {
    void queryClient.prefetchQuery({ queryKey: ["tools"], queryFn: () => toolsApi.list() });
    void queryClient.prefetchQuery({ queryKey: ["semantic-views"], queryFn: semanticViewsApi.list });
    void queryClient.prefetchQuery({
      queryKey: ["agents", "access", agentId],
      queryFn: () => agentAccessApi.list(agentId),
    });
    void queryClient.prefetchQuery({
      queryKey: ["agents", "observability", "sessions", agentId],
      queryFn: () => observabilityApi.sessions(agentId, 200),
    });
    void queryClient.prefetchQuery({
      queryKey: ["agents", "automations", agentId],
      queryFn: () => automationsApi.list(agentId),
    });
  }, [agentId, queryClient]);

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
    return <><Header fixed /><Main scroll><h1 className="text-lg font-semibold tracking-tight">Agent</h1><p role="alert" className="mt-1 mb-4 text-sm text-muted-foreground">Could not load this agent.</p>
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
        <div className="mb-6 flex shrink-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-lg font-semibold tracking-tight">
                {agent.name}
              </h1>
              <StateBadge status="active" />
            </div>
            <p className="mt-1 text-sm text-muted-foreground tabular-nums">
              {(agent.semantic_view_ids ?? []).length > 0
                ? `${agent.semantic_view_ids?.length} Semantic View${
                    agent.semantic_view_ids?.length === 1 ? "" : "s"
                  } · `
                : "No Semantic View · "}
              Updated{" "}
              {new Date(agent.updated_at).toLocaleString(undefined, {
                dateStyle: "medium",
                timeStyle: "short",
              })}
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

        {/* The page header and the tab row stay put; only the open tab's
            content scrolls. Configuration keeps its own sub-tab row fixed
            and scrolls below it. */}
        <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col gap-0">
          <TabsList className="h-auto max-w-full shrink-0 justify-start self-start overflow-x-auto [scrollbar-width:none] [&>[role=tab]]:flex-none">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="configuration">Configuration</TabsTrigger>
            <TabsTrigger value="access">Access</TabsTrigger>
            <TabsTrigger value="observability">Observability</TabsTrigger>
            <TabsTrigger value="improve">Improve</TabsTrigger>
            <TabsTrigger value="automations">Automations</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className={SCROLL}>
            <AgentOverviewTab agentId={agentId} />
          </TabsContent>
          <TabsContent
            value="configuration"
            forceMount
            className="mt-4 flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
          >
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
          <TabsContent value="access" className={SCROLL}>
            <AgentAccessTab agentId={agentId} agentName={agent.name}
              hasSemanticViews={(agent.semantic_view_ids ?? []).length > 0} />
          </TabsContent>
          <TabsContent value="observability" className={SCROLL}>
            <AgentObservabilityTab agentId={agentId} />
          </TabsContent>
          <TabsContent value="improve" className={SCROLL}>
            <AgentImproveTab agentId={agentId} />
          </TabsContent>
          <TabsContent value="automations" className={SCROLL}>
            <AgentAutomationsTab agentId={agentId} />
          </TabsContent>
        </Tabs>
      </Main>
    </>
  );
}
