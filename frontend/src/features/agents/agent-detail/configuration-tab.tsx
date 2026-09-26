import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Pencil, Plus, Save, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AgentModelSelect } from "./model-select";
import { semanticViewsApi, type SemanticView } from "@/features/intelligence/semantic-views-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  agentsApi,
  customToolsApi,
  mcpApi,
  skillsApi,
  toolsApi,
  type AgentCreateInput,
  type Agent,
  type CustomTool,
} from "@/features/agents/api";
import {
  CustomToolEditorDialog,
  type CustomToolDraft,
} from "@/features/agents/agent-detail/custom-tool-editor";

/** Tools a user agent may bundle; authoring tools are Nove-only. */
const BUNDLEABLE = new Set([
  "load_skill",
  "semantic_query",
  "semantic_search",
  "data_to_chart",
  "diagnose_change",
  "ml_execute",
]);

/**
 * Agent Configuration, split into the same sub-tabs as the detail surface:
 * General, Instructions, Tools, Skills, MCP. Each writes the same agent record;
 * the sub-tab is only a view.
 */
export function AgentConfigurationTab({
  agent,
  onSaved,
}: {
  agent: Agent;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<AgentCreateInput>(() =>
    toAgentDraft(agent),
  );
  useEffect(() => {
    setDraft(toAgentDraft(agent));
  }, [agent]);

  const save = useMutation({
    mutationFn: (body: AgentCreateInput) =>
      agentsApi.update(agent.agent_id, body),
    onSuccess: () => {
      toast.success("Agent saved");
      onSaved();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const set = <K extends keyof AgentCreateInput>(
    key: K,
    value: AgentCreateInput[K],
  ) => setDraft((prev) => ({ ...prev, [key]: value }));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end">
        <Button disabled={save.isPending} onClick={() => save.mutate(draft)}>
          <Save className="size-4" />
          Save changes
        </Button>
      </div>

      <Tabs defaultValue="general">
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="instructions">Instructions</TabsTrigger>
          <TabsTrigger value="tools">Tools</TabsTrigger>
          <TabsTrigger value="skills">Skills</TabsTrigger>
          <TabsTrigger value="mcp">MCP</TabsTrigger>
        </TabsList>

        <TabsContent value="general" className="mt-6 max-w-2xl space-y-5">
          <div className="space-y-1.5">
            <Label htmlFor="c-name">Display name</Label>
            <Input
              id="c-name"
              value={draft.name ?? ""}
              onChange={(e) => set("name", e.target.value)}
              placeholder="What users will see in the agent picker"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="c-desc">Description</Label>
            <Textarea
              id="c-desc"
              value={draft.description ?? ""}
              onChange={(e) => set("description", e.target.value)}
              placeholder="One line on what this agent is for"
              className="min-h-24"
            />
            <p className="text-xs text-muted-foreground">
              Shown to users in Nova Studio. It does not affect how the agent
              answers; the Instructions tab does that.
            </p>
          </div>
          <div className="rounded-lg border px-4 py-3">
            <p className="text-sm font-medium">
              Automatic runtime orchestration
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Nova adjusts routing, validation, and recovery to the provider and
              task. No manual mode selection is required.
            </p>
          </div>
        </TabsContent>

        <TabsContent value="instructions" className="mt-6 max-w-3xl space-y-4">
          <AgentModelSelect
            providerId={draft.model_provider_id ?? null}
            modelName={draft.model_name ?? null}
            onChange={(model_provider_id, model_name) =>
              setDraft((prev) => ({ ...prev, model_provider_id, model_name }))
            }
          />
          <div className="space-y-2">
            <Label htmlFor="c-orch">Orchestration instructions</Label>
            <Textarea
              id="c-orch"
              value={draft.instructions_orchestration ?? ""}
              onChange={(e) =>
                set("instructions_orchestration", e.target.value)
              }
              placeholder="Define how the agent reasons through tasks and chooses tools."
              className="min-h-48"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="c-resp">Response instructions</Label>
            <Textarea
              id="c-resp"
              value={draft.instructions_response ?? ""}
              onChange={(e) => set("instructions_response", e.target.value)}
              placeholder="Set rules for how the agent should sound and respond to users."
              className="min-h-32"
            />
          </div>
        </TabsContent>

        <TabsContent value="tools" className="mt-6">
          <ToolsConfig draft={draft} set={set} />
        </TabsContent>

        <TabsContent value="skills" className="mt-6">
          <SkillsConfig draft={draft} set={set} />
        </TabsContent>

        <TabsContent value="mcp" className="mt-6 max-w-2xl">
          <McpConfig />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ToolsConfig({
  draft,
  set,
}: {
  draft: AgentCreateInput;
  set: <K extends keyof AgentCreateInput>(
    key: K,
    value: AgentCreateInput[K],
  ) => void;
}) {
  const toolsQuery = useQuery({
    queryKey: ["tools"],
    queryFn: () => toolsApi.list(),
  });
  const viewsQuery = useQuery({
    queryKey: ["semantic-views"],
    queryFn: semanticViewsApi.list,
  });

  const toggle = (name: string) => {
    const current = new Set(draft.default_tools ?? []);
    if (current.has(name)) current.delete(name);
    else current.add(name);
    set(
      "default_tools",
      Array.from(current) as AgentCreateInput["default_tools"],
    );
  };

  const bundleable =
    toolsQuery.data?.tools.filter((t) => BUNDLEABLE.has(t.name)) ?? [];
  const mcpTools = toolsQuery.data?.tools.filter(
    (tool) => tool.source.startsWith("mcp:") && tool.is_enabled,
  ) ?? [];

  return (
    <div className="max-w-3xl space-y-6">
      <p className="text-sm text-muted-foreground">
        Choose the tools this agent can use during conversations.
        {" "}Data questions use the agent’s bound Semantic Views and configured tools.
        {" "}Use Nove for free-form SQL and database exploration.
      </p>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">Builtin tools</h3>
        <div className="grid gap-2 sm:grid-cols-2">
          {bundleable.map((tool) => (
            <label
              key={tool.name}
              className="flex items-start gap-2 rounded-xl border p-3 text-sm"
            >
              <Checkbox
                checked={(draft.default_tools ?? []).includes(tool.name)}
                onCheckedChange={() => toggle(tool.name)}
              />
              <span className="min-w-0">
                <span className="block font-mono text-xs font-medium">
                  {tool.name}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {tool.description}
                </span>
              </span>
            </label>
          ))}
        </div>
      </section>

      {mcpTools.length ? (
        <section className="space-y-2">
          <div>
            <h3 className="text-sm font-medium">MCP tools</h3>
            <p className="text-xs text-muted-foreground">External calls always require approval.</p>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {mcpTools.map((tool) => {
              const key = `mcp:${tool.tool_id}`;
              return (
                <label key={tool.tool_id} className="flex items-start gap-2 rounded-xl border p-3 text-sm">
                  <Checkbox
                    checked={(draft.default_tools ?? []).includes(key)}
                    onCheckedChange={() => toggle(key)}
                  />
                  <span className="min-w-0">
                    <span className="block break-all font-mono text-xs font-medium">{tool.name}</span>
                    <span className="block text-xs text-muted-foreground">{tool.description}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </section>
      ) : null}

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium">Query structured data</h3>
            <p className="text-xs text-muted-foreground">
              Add semantic views to query data using natural language. An agent
              may use more than one.
            </p>
          </div>
        </div>

        <AddSemanticView
          views={(viewsQuery.data ?? []).filter((view) =>
            view.status === "ACTIVE" && view.active_version !== null)}
          selected={draft.semantic_view_ids ?? []}
          onChange={(ids) => set("semantic_view_ids", ids)}
        />

        {viewsQuery.isPending ? <p className="text-xs text-muted-foreground">Loading Semantic Views…</p> : null}
        {viewsQuery.isError ? <p role="alert" className="text-xs text-destructive">Could not load Semantic Views. Try reopening this tab.</p> : null}

        {(draft.semantic_view_ids ?? []).length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No Semantic Views added. <Link to="/semantic-views" className="underline underline-offset-2">Publish a View</Link> before binding it to an agent.
          </p>
        ) : (
          <div className="space-y-2">
            {(draft.semantic_view_ids ?? []).map((id) => {
              const view = viewsQuery.data?.find(
                (item) => item.id === id,
              );
              return (
                <div
                  key={id}
                  className="flex items-start justify-between gap-3 rounded-2xl border p-4"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{view?.name ?? id}</span>
                      <Badge variant="outline">
                        {view?.status ?? "Unavailable"}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {view?.database_name ?? "View is no longer accessible"}
                      {view?.active_version ? ` · v${view.active_version}` : ""}
                    </p>
                  </div>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Remove ${view?.name ?? id}`}
                    onClick={() =>
                      set(
                        "semantic_view_ids",
                        (draft.semantic_view_ids ?? []).filter(
                          (x) => x !== id,
                        ),
                      )
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <CustomToolsSection draft={draft} set={set} />
    </div>
  );
}

function AddSemanticView({
  views,
  selected,
  onChange,
}: {
  views: SemanticView[];
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [pick, setPick] = useState<string>("");
  const available = views.filter(
    (view) => !selected.includes(view.id),
  );

  return (
    <div className="flex items-center gap-2">
      <Select value={pick} onValueChange={setPick}>
        <SelectTrigger className="max-w-xs" aria-label="Select a semantic view">
          <SelectValue placeholder="Select a semantic view" />
        </SelectTrigger>
        <SelectContent>
          {available.length === 0 ? (
            <SelectItem value="__none__" disabled>
              No more published Views
            </SelectItem>
          ) : (
            available.map((view) => (
              <SelectItem
                key={view.id}
                value={view.id}
              >
                {view.name} · {view.database_name} · v{view.active_version}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
      <Button
        variant="outline"
        aria-label="Add Semantic View"
        disabled={!pick || pick === "__none__"}
        onClick={() => {
          onChange([...selected, pick]);
          setPick("");
        }}
      >
        <Plus className="size-4" />
        Add
      </Button>
    </div>
  );
}

function CustomToolsSection({
  draft,
  set,
}: {
  draft: AgentCreateInput;
  set: <K extends keyof AgentCreateInput>(
    key: K,
    value: AgentCreateInput[K],
  ) => void;
}) {
  const queryClient = useQueryClient();
  const toolsQuery = useQuery({
    queryKey: ["custom-tools"],
    queryFn: () => customToolsApi.list(),
  });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<CustomTool | null>(null);

  const save = useMutation({
    mutationFn: (d: CustomToolDraft) => {
      const payload = {
        name: d.name.trim(),
        description: d.description.trim(),
        kind: "procedure" as const,
        database_name: d.database_name.trim() || null,
        function_name: null,
        definition: {
          parameters: d.parameters,
          statements: d.statements.filter((s) => s.trim()),
          output_mode: d.output_mode,
        },
      };
      return editing
        ? customToolsApi.update(editing.tool_id, payload)
        : customToolsApi.create(payload);
    },
    onSuccess: (_saved, submitted) => {
      if (editing && editing.name !== submitted.name) {
        const oldKey = `custom:${editing.name}`;
        const newKey = `custom:${submitted.name}`;
        set(
          "default_tools",
          (draft.default_tools ?? []).map((tool) =>
            tool === oldKey ? newKey : tool,
          ) as AgentCreateInput["default_tools"],
        );
      }
      toast.success(editing ? "Custom tool updated" : "Custom tool created");
      setOpen(false);
      setEditing(null);
      queryClient.invalidateQueries({ queryKey: ["custom-tools"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => customToolsApi.remove(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["custom-tools"] }),
  });

  const toggle = (name: string) => {
    const key = `custom:${name}`;
    const current = new Set(draft.default_tools ?? []);
    if (current.has(key)) current.delete(key);
    else current.add(key);
    set(
      "default_tools",
      Array.from(current) as AgentCreateInput["default_tools"],
    );
  };

  const tools = toolsQuery.data?.tools ?? [];

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Custom tools</h3>
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          <Plus className="size-4" />
          Add
        </Button>
      </div>
      {tools.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Add a StarRocks function or a Nova SQL procedure as a tool.
        </p>
      ) : (
        <div className="space-y-2">
          {tools.map((tool) => (
            <label
              key={tool.tool_id}
              className="flex items-start justify-between gap-2 rounded-xl border p-3 text-sm"
            >
              <span className="flex items-start gap-2">
                <Checkbox
                  checked={(draft.default_tools ?? []).includes(
                    `custom:${tool.name}`,
                  )}
                  onCheckedChange={() => toggle(tool.name)}
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-xs font-medium">
                      {tool.name}
                    </span>
                    <Badge variant="outline">{tool.kind}</Badge>
                  </span>
                  <span className="block text-xs text-muted-foreground">
                    {tool.description}
                  </span>
                </span>
              </span>
              <span className="flex items-center gap-1">
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => {
                    setEditing(tool);
                    setOpen(true);
                  }}
                  aria-label={`Edit ${tool.name}`}
                >
                  <Pencil className="size-4" />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => remove.mutate(tool.tool_id)}
                  aria-label={`Delete ${tool.name}`}
                >
                  <Trash2 className="size-4" />
                </Button>
              </span>
            </label>
          ))}
        </div>
      )}

      {open && (
        <CustomToolEditorDialog
          open={open}
          onOpenChange={(v) => {
            setOpen(v);
            if (!v) setEditing(null);
          }}
          initial={editing}
          submitting={save.isPending}
          onSubmit={(d) => save.mutate(d)}
        />
      )}
    </section>
  );
}

function SkillsConfig({
  draft,
  set,
}: {
  draft: AgentCreateInput;
  set: <K extends keyof AgentCreateInput>(
    key: K,
    value: AgentCreateInput[K],
  ) => void;
}) {
  const skillsQuery = useQuery({
    queryKey: ["skills"],
    queryFn: () => skillsApi.list(),
  });
  const skills = skillsQuery.data?.skills ?? [];

  const setMode = (name: string, mode: "off" | "default" | "discoverable") => {
    const defaults = new Set(draft.default_skills ?? []);
    const discoverable = new Set(draft.discoverable_skills ?? []);
    defaults.delete(name);
    discoverable.delete(name);
    if (mode === "default") defaults.add(name);
    if (mode === "discoverable") discoverable.add(name);
    set("default_skills", Array.from(defaults));
    set("discoverable_skills", Array.from(discoverable));
  };

  return (
    <div className="max-w-3xl space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">Skill availability</p>
          <p className="text-xs text-muted-foreground">
            Default skills load on every turn. Discoverable skills load only
            when the router finds them relevant.
          </p>
        </div>
        <Button asChild size="sm" variant="ghost">
          <Link to="/agents/skills">Manage in Skill Registry</Link>
        </Button>
      </div>
      {skillsQuery.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : skills.length === 0 ? (
        <p className="rounded-xl border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
          No skills yet. Add one in the Skill Registry.
        </p>
      ) : (
        <div className="divide-y rounded-lg border">
          {skills.map((skill) => {
            const mode = (draft.default_skills ?? []).includes(skill.name)
              ? "default"
              : (draft.discoverable_skills ?? []).includes(skill.name)
                ? "discoverable"
                : "off";
            return (
              <div
                key={skill.skill_id}
                className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs font-medium">
                      {skill.name}
                    </span>
                    <Badge variant="outline">
                      {skill.source === "builtin" ? "Platform" : "User"}
                    </Badge>
                  </div>
                  {skill.description ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {skill.description}
                    </p>
                  ) : null}
                </div>
                <Select
                  value={mode}
                  onValueChange={(value) =>
                    setMode(
                      skill.name,
                      value as "off" | "default" | "discoverable",
                    )
                  }
                >
                  <SelectTrigger
                    className="w-full sm:w-40"
                    aria-label={`${skill.name} mode`}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="off">Not available</SelectItem>
                    <SelectItem value="default">Default</SelectItem>
                    <SelectItem value="discoverable">Discoverable</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function toAgentDraft(agent: Agent): AgentCreateInput {
  const {
    agent_id: _agentId,
    owner_name: _ownerName,
    created_at: _createdAt,
    updated_at: _updatedAt,
    compiled_instructions: _compiledInstructions,
    semantic_model_id: _semanticModelId,
    semantic_model_ids: _semanticModelIds,
    ...draft
  } = agent;
  return {
    ...draft,
    default_tools: (agent.default_tools ?? []).filter((name) => name !== "query_execute"),
    semantic_view_ids: agent.semantic_view_ids ?? [],
    harness_mode: "auto",
  };
}

function McpConfig() {
  const serversQuery = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: () => mcpApi.list(),
  });
  const servers = serversQuery.data?.servers ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          MCP servers you have connected. Tools they expose appear under Tools.
        </p>
        <Button asChild size="sm" variant="ghost">
          <Link to="/agents/tools">Manage MCP servers</Link>
        </Button>
      </div>
      {serversQuery.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : servers.length === 0 ? (
        <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          No MCP servers connected.
        </div>
      ) : (
        <div className="divide-y rounded-xl border">
          {servers.map((server) => (
            <div
              key={server.server_id}
              className="flex items-center justify-between px-4 py-3"
            >
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{server.name}</span>
                <Badge variant="outline">{server.transport}</Badge>
              </div>
              {server.last_status ? (
                <Badge
                  variant={
                    server.last_status === "connected"
                      ? "secondary"
                      : "destructive"
                  }
                >
                  {server.last_status}
                </Badge>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
