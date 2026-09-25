import { useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { z } from "zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api-client";
import { useNoveSurface } from "@/features/assistant/nove-surface-hook";
import { defineNoveCapability } from "@/features/assistant/surface-registry";
import { Header } from "@/components/layout/header";
import { Main } from "@/components/layout/main";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { metadataApi } from "@/features/agents/metadata-api";
import { ColumnField, RelationField } from "./metadata-fields";
import { useRelationColumns } from "./use-relation-columns";
import { SemanticViewDetailPanel } from "./semantic-view-detail";
import { type SemanticDefinition, type SemanticView, type SemanticVersion } from "./semantic-views-api";
import { LoadingOverlay } from "@/components/ui/loading-overlay";

type Entity = {
  id: string;
  name: string;
  relation: string;
  key_columns: string[];
};
type SearchIndex = {
  name: string;
  source_relation: string;
  model_alias: string | null;
  active_version: number | null;
  status: string;
};
type FeatureView = {
  name: string;
  entity_id: string;
  active_version: number | null;
  status: string;
};
type FeatureGroup = {
  name: string;
  entity_id: string;
  active_version: number | null;
  status: string;
};
type SearchHit = { source_key: string; content: string; score: number };
type SearchResult = { hits: SearchHit[]; version: number; mode: string };
type Detail = { versions: { version: number; build_status: string }[] };

const splitColumns = (value: string) =>
  value.split(",").map((part) => part.trim()).filter(Boolean);

function editableSemanticDefinition(definition: SemanticDefinition): string {
  const expression = (value: string | object | undefined) =>
    typeof value === "string" ? { dialects: [{ dialect: "ANSI_SQL", expression: value }] } : value;
  return JSON.stringify({
    ...definition,
    datasets: definition.datasets?.map((dataset) => ({
      ...dataset,
      fields: dataset.fields?.map((field) => ({
        ...field, expression: expression(field.expression),
      })),
    })),
    metrics: definition.metrics?.map((metric) => ({
      ...metric, expression: expression(metric.expression),
    })),
  }, null, 2);
}

export function IntelligencePage({ section, semanticViewId }: {
  section: "entities" | "search" | "semantic" | "features";
  semanticViewId?: string;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const tab = section;
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [relation, setRelation] = useState("");
  const [semanticDatabase, setSemanticDatabase] = useState("");
  const [keys, setKeys] = useState("");
  const [content, setContent] = useState("");
  const [filters, setFilters] = useState("");
  const [modelAlias, setModelAlias] = useState("");
  const [selectedEntity, setSelectedEntity] = useState("");
  const [timestamp, setTimestamp] = useState("");
  const [definition, setDefinition] = useState("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"LEXICAL" | "SEMANTIC" | "HYBRID">("HYBRID");
  const [selectedSearch, setSelectedSearch] = useState("");
  const [confirmSearchDrop, setConfirmSearchDrop] = useState("");
  const selectedSemantic = semanticViewId ?? "";
  const [semanticDraft, setSemanticDraft] = useState("");
  const [editDefinitionOpen, setEditDefinitionOpen] = useState(false);
  const [semanticVersion, setSemanticVersion] = useState("");
  const [semanticMetrics, setSemanticMetrics] = useState("");
  const [semanticDimensions, setSemanticDimensions] = useState("");
  const [semanticNamedFilters, setSemanticNamedFilters] = useState("");
  const [acknowledgeRegressions, setAcknowledgeRegressions] = useState(false);
  const [confirmSemanticDeprecate, setConfirmSemanticDeprecate] = useState(false);
  const [confirmSemanticDrop, setConfirmSemanticDrop] = useState(false);
  const [selectedGroup, setSelectedGroup] = useState("");
  const [lookupValues, setLookupValues] = useState<Record<string, string>>({});
  const [selectedFeatureView, setSelectedFeatureView] = useState("");
  const [readyFeature, setReadyFeature] = useState<{ name: string; version: number } | null>(null);
  const [labelRelation, setLabelRelation] = useState("");
  const [labelTimestamp, setLabelTimestamp] = useState("");
  const [targetColumn, setTargetColumn] = useState("");
  const [modelName, setModelName] = useState("");
  const [modelType, setModelType] = useState<"classification" | "regression">("classification");
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [featureResult, setFeatureResult] = useState<unknown>(null);
  const [semanticResult, setSemanticResult] = useState<unknown>(null);

  const entities = useQuery({
    queryKey: ["intelligence", "entities"],
    queryFn: () => api.get<Entity[]>("/entities"),
    enabled: tab === "entities" || tab === "features",
  });
  const indexes = useQuery({
    queryKey: ["intelligence", "search"],
    queryFn: () => api.get<SearchIndex[]>("/ai/search"),
    enabled: tab === "search",
  });
  const semantic = useQuery({
    queryKey: ["intelligence", "semantic"],
    queryFn: () => api.get<SemanticView[]>("/semantic-views"),
    enabled: tab === "semantic" && !semanticViewId,
  });
  const views = useQuery({
    queryKey: ["intelligence", "feature-views"],
    queryFn: () => api.get<FeatureView[]>("/features/views"),
    enabled: tab === "features",
  });
  const groups = useQuery({
    queryKey: ["intelligence", "feature-groups"],
    queryFn: () => api.get<FeatureGroup[]>("/features/groups"),
    enabled: tab === "features",
  });
  const searchDetail = useQuery({
    queryKey: ["intelligence", "search", selectedSearch],
    queryFn: () => api.get<Detail>(`/ai/search/${encodeURIComponent(selectedSearch)}`),
    enabled: tab === "search" && Boolean(selectedSearch),
  });
  const semanticDetail = useQuery({
    queryKey: ["intelligence", "semantic", selectedSemantic],
    queryFn: () => api.get<SemanticView & { versions: SemanticVersion[] }>(
      `/semantic-views/${encodeURIComponent(selectedSemantic)}`,
    ),
    enabled: tab === "semantic" && Boolean(selectedSemantic),
  });
  const databases = useQuery({
    queryKey: ["intelligence", "databases"],
    queryFn: () => metadataApi.listDatabases(true),
    enabled: tab === "semantic",
  });
  const embeddingModels = useQuery({
    queryKey: ["intelligence", "embedding-models"],
    enabled: tab === "search",
    queryFn: async () => {
      const providers = await api.get<{ providers: { id: string }[] }>("/ai/providers");
      const lists = await Promise.all(providers.providers.map((provider) =>
        api.get<{ models: { type: string; logical_alias: string | null; is_active: boolean }[] }>(
          `/ai/providers/${encodeURIComponent(provider.id)}/models`,
        ),
      ));
      return lists.flatMap((list) => list.models)
        .filter((model) => model.type === "embedding" && model.is_active && model.logical_alias)
        .map((model) => model.logical_alias as string).sort();
    },
  });

  const run = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true);
    try {
      await action();
      await queryClient.invalidateQueries({ queryKey: ["intelligence"] });
      toast.success(success);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The request failed");
    } finally {
      setBusy(false);
    }
  };

  const entity = (entities.data ?? []).find((item) => item.id === selectedEntity);
  const selectedSearchIndex = (indexes.data ?? []).find((item) => item.name === selectedSearch);
  const group = (groups.data ?? []).find((item) => item.name === selectedGroup);
  const groupEntity = (entities.data ?? []).find((item) => item.id === group?.entity_id);
  const sourceColumns = useRelationColumns(relation);
  const entityColumns = useRelationColumns(entity?.relation ?? "");
  const labelColumns = useRelationColumns(labelRelation);
  const sourceColumnNames = sourceColumns.data?.columns.map((column) => column.name) ?? [];
  const entityColumnNames = entityColumns.data?.columns.map((column) => column.name) ?? [];
  const labelColumnNames = labelColumns.data?.columns.map((column) => column.name) ?? [];
  const currentSemanticVersion = (semanticDetail.data?.versions ?? []).find((item) =>
    item.version === Number(semanticVersion || semanticDetail.data?.active_version))
    ?? semanticDetail.data?.versions[0];
  const semanticMetricNames = Array.isArray(currentSemanticVersion?.definition.metrics)
    ? currentSemanticVersion.definition.metrics.map((item) => item.name) : [];
  const semanticNamedFilterNames = Array.isArray(currentSemanticVersion?.definition.named_filters)
    ? currentSemanticVersion.definition.named_filters.map((item) => item.name) : [];
  const semanticDimensionNames = Array.isArray(currentSemanticVersion?.definition.datasets)
    ? currentSemanticVersion.definition.datasets.flatMap((dataset) =>
    (Array.isArray(dataset.fields) ? dataset.fields : []).filter((field) => field.kind === "dimension" ||
      field.dimension || ["Date", "Time", "DateTime", "DateTimeTz"].includes(field.datatype ?? ""))
      .map((field) => `${dataset.name}.${field.name}`)) : [];
  const selectedSemanticView = semanticDetail.data ??
    (semantic.data ?? []).find((item) => item.id === selectedSemantic);
  const pageTitle = tab === "features" ? "Feature Store" : tab === "semantic"
    ? semanticViewId ? selectedSemanticView?.name ?? "Semantic View" : "Semantic Views"
    : tab === "search" ? "AI Search" : "Entities";
  const { askNove, publishEvent } = useNoveSurface({
    id: `intelligence.${tab}`,
    route: typeof window === "undefined" ? "/semantic-views" : window.location.pathname,
    title: pageTitle,
    context: () => ({
      entity: tab === "semantic" && selectedSemanticView ? {
        type: "semantic_view",
        id: selectedSemanticView.id,
        name: selectedSemanticView.name,
        metadata: {
          status: selectedSemanticView.status,
          activeVersion: selectedSemanticView.active_version,
          metricCount: semanticMetricNames.length,
          dimensionCount: semanticDimensionNames.length,
        },
      } : undefined,
      view: { activeTab: tab, filters: { version: semanticVersion || null } },
      domain: { database: selectedSemanticView?.database_name ?? null },
    }),
    capabilities: [
      defineNoveCapability({
        name: "tab.open", risk: "safe", mayChangeSurface: true,
        argsSchema: z.object({ tab: z.enum(["entities", "search", "semantic", "features"]) }),
        execute: ({ tab: next }) => navigate({ to: {
          entities: "/entities", search: "/ai-search",
          semantic: "/semantic-views", features: "/feature-store",
        }[next] }),
      }),
      defineNoveCapability({
        name: "surface.refresh", risk: "safe", argsSchema: z.object({}),
        execute: () => tab === "semantic" && semanticViewId
          ? semanticDetail.refetch({ throwOnError: true })
          : tab === "semantic"
          ? semantic.refetch({ throwOnError: true })
          : queryClient.invalidateQueries(
              { queryKey: ["intelligence"] },
              { throwOnError: true },
            ),
      }),
      defineNoveCapability({
        name: "surface.select", risk: "safe", argsSchema: z.object({ id: z.string().min(1).max(160) }),
        execute: ({ id }) => {
          if (tab !== "semantic" || !id.trim()) {
            throw new Error("Choose a Semantic View first.");
          }
          return navigate({ to: "/semantic-views/$viewId", params: { viewId: id } });
        },
      }),
    ],
    suggestedActions: tab === "semantic" ? [
      { label: "Explain this view", prompt: "Explain the selected Semantic View and its metrics." },
      { label: "Review model", prompt: "Review this Semantic View for missing relationships or metrics." },
      { label: "Help validate", prompt: "Help me validate this Semantic View before publishing." },
    ] : [
      { label: `Explore ${pageTitle}`, prompt: `Help me use ${pageTitle} in Nova.` },
    ],
  });

  const pageDescription = {
    entities: "Define how a record is identified so search and features can use the same key.",
    search: "Find records by their text, then create an index when you need to search another table.",
    semantic: semanticViewId
      ? "Inspect the definition, check a question, and manage published versions."
      : "Give business questions a shared meaning. Build a view, check it, then publish it for agents.",
    features: "Turn time-based table columns into reusable inputs for lookups and models.",
  }[tab];

  return (
    <>
      <Header fixed>
        {tab === "semantic" && semanticViewId ? <Link to="/semantic-views"
          className="inline-flex min-h-10 items-center gap-2 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
          <ArrowLeft className="size-4" aria-hidden="true" /> All Semantic Views
        </Link> : null}
      </Header>
      <Main scroll fluid className="px-5 py-6 md:px-10 md:py-8">
      <div className="mx-auto w-full max-w-5xl space-y-7">
         <div className="flex flex-wrap items-start justify-between gap-3">
           <div>
            <h1 className="text-2xl font-heading">{pageTitle}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{pageDescription}</p>
           </div>
           <Button variant="outline" size="sm" onClick={() => void askNove(
             tab === "semantic" && selectedSemantic
               ? "Explain this Semantic View and suggest any missing relationships."
               : `Help me use ${pageTitle} in Nova.`,
           )}>Ask Nove</Button>
         </div>
        {tab === "entities" && (
          <section className="space-y-5" aria-label="Entities">
            <div className="rounded-lg border bg-surface-1 p-5">
              <h2 className="text-lg font-medium">Identify one record</h2>
              <p className="mt-1 text-sm text-muted-foreground">An entity tells Nova which column uniquely identifies a customer, order, or other record. Feature Store uses that key to find the right values.</p>
            </div>
            <h2 className="font-medium">Create an entity</h2>
            <div className="grid gap-3 rounded-lg border p-4 md:grid-cols-2">
              <Field label="Entity name" value={name} onChange={setName} />
              <RelationField label="Source relation" value={relation}
                onChange={(next) => { setRelation(next); setKeys("") }} />
              <ColumnField label="Unique key columns" value={keys} onChange={setKeys}
                options={sourceColumnNames} multiple />
              <div className="flex items-end">
                <Button disabled={busy || !name || !relation || !keys} onClick={() => void run(
                  () => api.post("/entities", {
                    name, relation,
                    database: relation.split(".")[relation.split(".").length - 2],
                    key_columns: splitColumns(keys),
                  }), "Entity created")}>Create entity</Button>
              </div>
            </div>
            <h2 className="font-medium">Existing entities</h2>
            <ObjectList loading={entities.isPending} error={entities.isError}
              empty="No entities yet. Choose a source table and its unique key above." retry={() => void entities.refetch()}>
              {(entities.data ?? []).map((item) => (
                <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                  <div><b>{item.name}</b><p className="text-sm text-muted-foreground">
                    {item.relation} · {item.key_columns.join(", ")}</p></div>
                  <Button variant="ghost" disabled={busy} onClick={() => void run(
                    () => api.delete(`/entities/${encodeURIComponent(item.id)}`),
                    "Entity deprecated")}>Deprecate</Button>
                </li>
              ))}
            </ObjectList>
          </section>
        )}

        {tab === "search" && (
          <section className="space-y-5" aria-label="AI Search">
            <div className="rounded-lg border bg-surface-1 p-5">
              <h2 className="text-lg font-medium">Search your data</h2>
              <p className="mt-1 text-sm text-muted-foreground">Choose an index below, enter words or a question, and review the matching records. An index decides which table and text columns can be searched.</p>
            </div>
            <div>
              <h2 className="font-medium">Available indexes</h2>
              <p className="text-sm text-muted-foreground">Select one to search it or check its build status.</p>
            </div>
            <ObjectList loading={indexes.isPending} error={indexes.isError}
              empty="No search indexes yet. Create one from a table below."
              retry={() => void indexes.refetch()}>
              {(indexes.data ?? []).map((item) => (
                <li key={item.name} className="flex flex-wrap items-center justify-between gap-2 py-3">
                  <button className="rounded-md p-2 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                    aria-current={selectedSearch === item.name ? "true" : undefined}
                    onClick={() => { setSelectedSearch(item.name); setSearchResult(null); setMode("HYBRID"); }}>
                    <b>{item.name}</b><p className="text-sm text-muted-foreground">
                      {item.source_relation} · {item.status} · v{item.active_version ?? "—"}</p>
                  </button>
                  <div className="flex gap-2">
                    <Button variant="outline" disabled={busy} onClick={() => void run(
                      () => api.post(`/ai/search/${encodeURIComponent(item.name)}/rebuild`, {}),
                      "Rebuild queued")}>Rebuild</Button>
                    <Button variant="ghost" className="text-destructive" disabled={busy}
                      onClick={() => setConfirmSearchDrop(item.name)}>Delete</Button>
                  </div>
                </li>
              ))}
            </ObjectList>
            {selectedSearch && <div className="space-y-3 rounded-lg border p-4">
              <h2 className="font-medium">Search {selectedSearch}</h2>
              <p className="text-sm text-muted-foreground">Searches return records from the source table. Hybrid combines text and meaning when an embedding model is available.</p>
              {!selectedSearchIndex?.active_version && <p role="status" className="rounded-md bg-muted p-3 text-sm">This index has no active version yet. Wait for the build or activate a ready version below.</p>}
              <div className="flex flex-wrap gap-2">
                <Input aria-label="Search query" placeholder="Enter words or a question" className="min-w-40 flex-1" value={query}
                  onChange={(event) => setQuery(event.target.value)} />
                <label className="flex items-center gap-2 text-sm">Mode
                  <select aria-label="Search mode" value={mode}
                    onChange={(event) => setMode(event.target.value as typeof mode)}
                    className="h-9 rounded-md border bg-background px-3 text-sm">
                    <option value="HYBRID">Hybrid (text + meaning)</option>
                    <option value="LEXICAL">Text match</option>
                    <option value="SEMANTIC" disabled={!selectedSearchIndex?.model_alias}>Meaning match</option>
                  </select>
                </label>
                <Button disabled={busy || !query.trim() || !selectedSearchIndex?.active_version} onClick={() => void run(async () => {
                  const result = await api.post<SearchResult>(
                    `/ai/search/${encodeURIComponent(selectedSearch)}/query`,
                    { query, mode },
                  );
                  setSearchResult(result);
                }, "Search complete")}>Search</Button>
              </div>
              {searchResult && (searchResult.hits.length ? <ul className="divide-y" aria-label="Search results">
                {searchResult.hits.map((hit) => <li key={hit.source_key} className="py-3">
                  <p className="text-sm">{hit.content}</p>
                  <p className="text-xs text-muted-foreground">{hit.source_key}</p>
                </li>)}
              </ul> : <p role="status" className="rounded-md bg-muted p-3 text-sm">No matching records. Try another term or search mode.</p>)}
              {(searchDetail.data?.versions ?? []).map((version) =>
                <div key={version.version} className="flex items-center justify-between text-sm">
                  <span>Version {version.version} · {version.build_status}</span>
                  {version.build_status === "READY" && <Button variant="outline" disabled={busy}
                    onClick={() => void run(() => api.post(
                      `/ai/search/${encodeURIComponent(selectedSearch)}/versions/${version.version}/activate`,
                    ), "Search version activated")}>Activate</Button>}
                  {version.build_status === "FAILED" && <Button variant="outline" disabled={busy}
                    onClick={() => void run(() => api.post(
                      `/ai/search/${encodeURIComponent(selectedSearch)}/versions/${version.version}/retry`,
                    ), "Build retry queued")}>Retry</Button>}
                </div>)}
            </div>}
            <ConfirmDialog open={Boolean(confirmSearchDrop)} onOpenChange={(open) => {
              if (!open) setConfirmSearchDrop("");
            }} title="Delete search index"
              desc={`Delete ${confirmSearchDrop}? Its indexed records and versions will no longer be searchable.`}
              confirmText="Delete" destructive isLoading={busy}
              handleConfirm={() => {
                const indexName = confirmSearchDrop;
                setConfirmSearchDrop("");
                void run(async () => {
                  await api.delete(`/ai/search/${encodeURIComponent(indexName)}`);
                  if (selectedSearch === indexName) { setSelectedSearch(""); setSearchResult(null); }
                }, "Search index deleted");
              }} />
            <details className="rounded-lg border p-4">
              <summary className="cursor-pointer font-medium">Create a search index</summary>
              <p className="my-3 text-sm text-muted-foreground">Choose a table, its unique key, and the columns containing searchable text. Filter columns and an embedding model are optional.</p>
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Index name" value={name} onChange={setName} />
               <RelationField label="Source relation" value={relation}
                 onChange={(next) => {
                   setRelation(next); setKeys(""); setContent(""); setFilters("");
                 }} />
               <ColumnField label="Key columns" value={keys} onChange={setKeys}
                 options={sourceColumnNames} multiple />
               <ColumnField label="Search text columns" value={content} onChange={setContent}
                 options={sourceColumnNames.filter((column) => !splitColumns(keys).includes(column))} multiple />
               <ColumnField label="Filter columns" value={filters} onChange={setFilters}
                 options={sourceColumnNames.filter((column) =>
                   !splitColumns(keys).includes(column) && !splitColumns(content).includes(column))} multiple />
               <div className="space-y-1 text-sm"><span>Embedding model</span>
                 <SearchableSelect label="Embedding model" options={embeddingModels.data ?? []}
                   value={modelAlias} onChange={setModelAlias} emptyLabel="Lexical only"
                   className="h-9 w-full justify-start" />
               </div>
              <div className="md:col-span-2">
                <Button disabled={busy || !name || !relation || !keys || !content}
                  onClick={() => void run(() => api.post("/ai/search", {
                    name, source_relation: relation, key_columns: splitColumns(keys),
                    content_columns: splitColumns(content), filter_columns: splitColumns(filters),
                    model_alias: modelAlias || null,
                  }), "Search index created; build queued")}>Create search index</Button>
              </div>
            </div>
            </details>
          </section>
        )}

        {tab === "semantic" && !semanticViewId && <section className="space-y-6" aria-label="Semantic Views">
          <div className="space-y-4 rounded-lg border bg-surface-1 p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="max-w-xl space-y-2">
                <h2 className="text-lg font-semibold">Create a shared definition for your data</h2>
                <p className="text-sm text-muted-foreground">A Semantic View connects tables, names business measures, and gives agents a consistent way to answer questions.</p>
              </div>
              <Button asChild><Link to="/semantic-views/builder">Build visually</Link></Button>
            </div>
            <ol className="grid gap-3 text-sm sm:grid-cols-3">
              <li><b>1. Build</b><p className="text-muted-foreground">Choose tables, fields, and measures.</p></li>
              <li><b>2. Check</b><p className="text-muted-foreground">Validate a draft and preview a question.</p></li>
              <li><b>3. Publish</b><p className="text-muted-foreground">Make the checked version available to agents.</p></li>
            </ol>
          </div>
          <div>
            <h2 className="text-lg font-medium">Your views</h2>
            <p className="text-sm text-muted-foreground">Open a view to inspect its definition, test questions, and manage versions.</p>
          </div>
          <ObjectList loading={semantic.isPending} error={semantic.isError}
            empty="No Semantic Views yet. Build one visually to get started."
            loadingLabel="Loading Semantic Views"
            errorLabel="Could not load Semantic Views."
            retry={() => void semantic.refetch()}>
            {(semantic.data ?? []).map((item) => <li key={item.id} className="py-1">
              <Link to="/semantic-views/$viewId" params={{ viewId: item.id }}
                className="flex min-h-16 items-center justify-between gap-4 rounded-md px-3 py-2 hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                onClick={() => publishEvent({ source: "user", type: "entity_selected", payload: { entityId: item.id, entityType: "semantic_view" } })}>
                <span className="min-w-0"><span className="block truncate font-medium">{item.name}</span>
                  <span className="block truncate text-sm text-muted-foreground">{item.database_name || "No database"}</span></span>
                <span className="flex shrink-0 items-center gap-3 text-sm text-muted-foreground">
                  <span>{item.status === "DEPRECATED" ? "Deprecated"
                    : item.active_version ? `Published · v${item.active_version}` : "Draft"}</span>
                  <ArrowRight className="size-4" aria-hidden="true" />
                </span>
              </Link>
            </li>)}
          </ObjectList>
          <details className="rounded-lg border p-4">
            <summary className="cursor-pointer font-medium">Import a definition (advanced)</summary>
            <p className="mb-4 mt-2 text-sm text-muted-foreground">If you already have an Ossie YAML or JSON definition, paste it here. For a first view, use the visual builder above.</p>
            <div className="grid gap-3 md:grid-cols-2">
            <Field label="View name" value={name} onChange={setName} />
             <div className="space-y-1 text-sm"><span>Database</span>
               <SearchableSelect label="Semantic View database" options={databases.data ?? []}
                 value={semanticDatabase} onChange={setSemanticDatabase}
                 allowEmpty={false} emptyLabel="Choose database"
                 className="h-9 w-full justify-start" />
             </div>
             <label className="space-y-1 text-sm md:col-span-2">Ossie definition (YAML or JSON)
              <Textarea rows={9} value={definition}
                onChange={(event) => setDefinition(event.target.value)} />
            </label>
             <Button disabled={busy || !name || !semanticDatabase || !definition}
               onClick={() => void run(() => api.post("/semantic-views", {
                 name, database: semanticDatabase, definition,
              }), "Semantic View created")}>Create draft</Button>
            </div>
          </details>
        </section>}

        {tab === "semantic" && semanticViewId && <section className="min-w-0 space-y-6" aria-label="Semantic View details">
            {semanticDetail.isPending ? <LoadingOverlay label="Loading Semantic View details" /> : null}
            {semanticDetail.isError ? <div role="alert" className="space-y-2 rounded-lg border border-destructive p-4 text-sm">
              <p>Could not load this Semantic View.</p><Button variant="outline" onClick={() => void semanticDetail.refetch()}>Retry</Button>
            </div> : null}
            {semanticDetail.data ? <div className="min-w-0 space-y-6">
              <dl className="grid gap-4 rounded-lg border bg-surface-1 p-4 text-sm sm:grid-cols-3 sm:p-5">
                <div><dt className="text-muted-foreground">Availability</dt><dd className="mt-1 font-medium">{selectedSemanticView?.status === "DEPRECATED" ? "Deprecated · unavailable to agents"
                  : selectedSemanticView?.active_version ? "Published for agents" : "Draft · not available to agents"}</dd></div>
                <div><dt className="text-muted-foreground">Database</dt><dd className="mt-1 break-all font-medium">{selectedSemanticView?.database_name || "Unavailable"}</dd></div>
                <div><dt className="text-muted-foreground">Published version</dt><dd className="mt-1 font-medium">{selectedSemanticView?.active_version ? `v${selectedSemanticView.active_version}` : "None yet"}</dd></div>
              </dl>
              {semanticDetail.data && currentSemanticVersion ? <section className="min-w-0 space-y-4 rounded-lg border bg-background p-4 sm:p-5" aria-label="Inspect a version">
                <label className="block max-w-xs space-y-1 text-sm">Inspect version
                  <select aria-label="Semantic query version" value={semanticVersion}
                    onChange={(event) => {
                      setSemanticVersion(event.target.value);
                      setSemanticResult(null);
                    }}
                    className="flex h-10 w-full rounded-md border bg-background px-3 text-sm">
                    <option value="">Current version</option>
                    {semanticDetail.data.versions.map((item) =>
                      <option key={item.version} value={item.version}>v{item.version} · {item.status}</option>)}
                  </select>
                </label>
                <SemanticViewDetailPanel
                  key={`${selectedSemantic}-${currentSemanticVersion.version}`}
                  view={{ ...semanticDetail.data, id: selectedSemantic }}
                  version={currentSemanticVersion}
                  onVersionCreated={(version) => setSemanticVersion(String(version))}
                />
              </section> : null}
              <section className="space-y-4 rounded-lg border bg-background p-4 sm:p-5" aria-label="Version history">
                <div><h2 className="text-lg font-medium">Version history</h2>
                  <p className="text-sm text-muted-foreground">Validate a draft, review changes to verified questions, then publish it for agents.</p></div>
              {semanticDetail.data && semanticDetail.data.versions.length === 0 ? <p className="text-sm text-muted-foreground">No versions saved for this View.</p> : null}
              {(semanticDetail.data?.versions ?? []).map((version) =>
                <div key={version.version} className="space-y-2 border-t pt-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="mr-auto">v{version.version} · {version.status}{version.version === semanticDetail.data?.active_version ? " · Used by agents" : ""}</span>
                   <Button variant="ghost" onClick={() => {
                     setSemanticDraft(version.validation?.migration?.raw_definition_preserved
                       ? JSON.stringify(version.validation.migration.raw_definition ?? version.definition, null, 2)
                       : editableSemanticDefinition(version.definition));
                     setEditDefinitionOpen(true);
                   }}>
                     {version.validation?.migration?.raw_definition_preserved ? "Copy original to editor" : "Use as draft"}
                   </Button>
                   {version.status === "DRAFT" && !version.validation?.migration?.raw_definition_preserved && <Button variant="outline" disabled={busy}
                     onClick={() => void run(() => api.post(
                       `/semantic-views/${encodeURIComponent(selectedSemantic)}/versions/${version.version}/validate`,
                     ), "Validation complete")}>Validate</Button>}
                   {version.status === "VALIDATED" && <Button variant="outline" disabled={busy ||
                     Boolean(version.validation?.regression && version.validation.regression.changed > 0 && !acknowledgeRegressions)}
                     onClick={() => void run(() => api.post(
                       `/semantic-views/${encodeURIComponent(selectedSemantic)}/versions/${version.version}/publish`,
                       { acknowledge_regressions: acknowledgeRegressions },
                      ), "Version published. Verify agent access again before running bound agents.")}>Publish</Button>}
                 </div>
                  {version.validation?.migration?.raw_definition_preserved ? <p className="rounded-md border border-warning bg-warning/10 px-3 py-2 text-sm">Legacy format: add a supported Ossie 0.1.1 version. This imported version is for review only.</p> : null}
                  {version.validation && <div className="space-y-1 rounded-md bg-muted p-3">
                   <p>{version.validation.valid ? "Validated" : "Validation failed"}
                     {version.validation.regression &&
                       ` · ${version.validation.regression.changed} regression changes`}</p>
                   {version.validation.errors.map((error, index) =>
                     <p key={`error-${index}`} className="text-destructive">{error}</p>)}
                   {version.validation.warnings.map((warning, index) =>
                     <p key={`warning-${index}`} className="text-muted-foreground">{warning}</p>)}
                   {version.validation.regression?.cases.map((item) =>
                     <p key={item.verified_query_id}>{item.question}: {item.status}</p>)}
                   {version.validation.regression && version.validation.regression.changed > 0 &&
                     <label className="flex items-center gap-2 pt-2">
                       <input type="checkbox" checked={acknowledgeRegressions}
                         onChange={(event) => setAcknowledgeRegressions(event.target.checked)} />
                       Acknowledge verified-query changes before publishing
                     </label>}
                  </div>}
                </div>)}
              </section>
              <details open={editDefinitionOpen}
                onToggle={(event) => setEditDefinitionOpen(event.currentTarget.open)}
                className="space-y-3 rounded-lg border p-4">
                <summary className="cursor-pointer font-medium">Edit definition directly (advanced)</summary>
                <p className="text-sm text-muted-foreground">Use an existing version as a draft above, or paste an Ossie YAML or JSON definition. This creates a new version for validation.</p>
                <label className="block space-y-1 text-sm">New version definition (Ossie YAML or JSON)
                  <Textarea rows={9} value={semanticDraft}
                    onChange={(event) => setSemanticDraft(event.target.value)} />
                </label>
                <Button disabled={busy || !semanticDraft.trim()} onClick={() => void run(
                  () => api.post(`/semantic-views/${encodeURIComponent(selectedSemantic)}/versions`,
                    { definition: semanticDraft }), "Draft version created")}>Add version</Button>
              </details>
              <section className="space-y-4 rounded-lg border bg-background p-4 sm:p-5" aria-label="Run a published version">
              <div><h2 className="text-lg font-medium">Run a published version</h2>
                <p className="text-sm text-muted-foreground">Choose a measure and optional dimensions to see results from the published data.</p></div>
              <div className="grid gap-3 md:grid-cols-2">
               <ColumnField label="Metrics" value={semanticMetrics} onChange={setSemanticMetrics}
                 options={semanticMetricNames} multiple />
               <ColumnField label="Dimensions" value={semanticDimensions} onChange={setSemanticDimensions}
                 options={semanticDimensionNames} multiple />
               <ColumnField label="Named filters" value={semanticNamedFilters}
                 onChange={setSemanticNamedFilters} options={semanticNamedFilterNames} multiple />
              </div>
              <Button disabled={busy || !semanticMetrics && !semanticDimensions ||
                !currentSemanticVersion || !["ACTIVE", "DEPRECATED"].includes(currentSemanticVersion.status)}
               onClick={() => void run(async () => {
               const result = await api.post(`/semantic-views/${encodeURIComponent(selectedSemantic)}/query`,
                 { metrics: splitColumns(semanticMetrics), dimensions: splitColumns(semanticDimensions),
                   named_filters: splitColumns(semanticNamedFilters),
                   version: semanticVersion ? Number(semanticVersion) : null });
               setSemanticResult(result);
             }, "Semantic query complete")}>Run query</Button>
             {semanticResult !== null && <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
               {JSON.stringify(semanticResult, null, 2)}</pre>}
              </section>
              <details className="rounded-lg border bg-background p-4 sm:p-5">
                <summary className="cursor-pointer font-medium">Manage this view</summary>
                <p className="mt-2 text-sm text-muted-foreground">Deprecating or deleting a view stops agents from using it.</p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {selectedSemanticView?.status !== "DEPRECATED" ? <Button variant="outline" disabled={busy} onClick={() => setConfirmSemanticDeprecate(true)}>Deprecate</Button> : null}
                  <Button variant="destructive" disabled={busy} onClick={() => setConfirmSemanticDrop(true)}>Delete</Button>
                </div>
              </details>
              <ConfirmDialog open={confirmSemanticDeprecate} onOpenChange={setConfirmSemanticDeprecate}
                title="Deprecate Semantic View"
                desc="Agents using this View will stop using it."
                confirmText="Deprecate" destructive isLoading={busy}
                handleConfirm={() => {
                  setConfirmSemanticDeprecate(false);
                  void run(async () => {
                    await api.post(`/semantic-views/${encodeURIComponent(selectedSemantic)}/deprecate`, {});
                    await navigate({ to: "/semantic-views" });
                  }, "Semantic View deprecated");
                }} />
              <ConfirmDialog open={confirmSemanticDrop} onOpenChange={setConfirmSemanticDrop}
                title="Delete Semantic View"
                desc="This removes every version. Agents using this View will stop using it."
               confirmText="Delete" destructive isLoading={busy}
               handleConfirm={() => {
                 setConfirmSemanticDrop(false);
                 void run(async () => {
                   await api.delete(`/semantic-views/${encodeURIComponent(selectedSemantic)}`);
                   await navigate({ to: "/semantic-views" });
                 }, "Semantic View deleted");
               }} />
           </div> : null}
        </section>}

        {tab === "features" && <section className="space-y-5" aria-label="Feature Store">
          <div className="space-y-3 rounded-lg border bg-surface-1 p-5">
            <h2 className="text-lg font-medium">Prepare data for models</h2>
            <p className="text-sm text-muted-foreground">A Feature View saves values from a table at a point in time. A Feature Group combines active views so you can look up values or train a model.</p>
            <ol className="grid gap-3 text-sm sm:grid-cols-3">
              <li><b>1. Identify records</b><p className="text-muted-foreground">Create an entity with a unique key. <Link className="underline underline-offset-2" to="/entities">Manage entities</Link></p></li>
              <li><b>2. Create a view</b><p className="text-muted-foreground">Choose a time column and the values to save.</p></li>
              <li><b>3. Use a group</b><p className="text-muted-foreground">Activate the view, group it, then look up values or train.</p></li>
            </ol>
          </div>
          <div>
            <h2 className="font-medium">Create a Feature View</h2>
            <p className="text-sm text-muted-foreground">Choose the entity first. Nova will show columns from its source table.</p>
          </div>
          {entities.data?.length === 0 && <p role="status" className="rounded-lg border p-4 text-sm">No entities found. <Link className="underline underline-offset-2" to="/entities">Create an entity</Link> before making a Feature View.</p>}
          <div className="grid gap-3 rounded-lg border p-4 md:grid-cols-2">
            <Field label="Feature View name" value={name} onChange={setName} />
            <label className="space-y-1 text-sm">Entity for new view
              <select aria-label="Entity for new view" value={selectedEntity}
                onChange={(event) => { setSelectedEntity(event.target.value); setSelectedFeatureView(""); }}
                className="flex h-9 w-full rounded-md border bg-background px-3 text-sm">
                <option value="">Choose an entity</option>
                {(entities.data ?? []).map((item) => <option value={item.id} key={item.id}>
                  {item.name}</option>)}
              </select>
            </label>
             <ColumnField label="Event timestamp column" value={timestamp}
               onChange={setTimestamp} options={entityColumnNames} />
             <ColumnField label="Feature columns" value={content} onChange={setContent}
               options={entityColumnNames.filter((column) =>
                 column !== timestamp && !entity?.key_columns.includes(column))} multiple />
            <Button disabled={busy || !name || !entity || !timestamp || !content}
              onClick={() => void run(() => api.post("/features/views", {
                name, entity_id: entity?.id, source_relation: entity?.relation,
                event_timestamp: timestamp, feature_columns: splitColumns(content),
              }), "Feature View materialized")}>Create Feature View</Button>
          </div>
          <h2 className="font-medium">Feature Views</h2>
          <ObjectList loading={views.isPending} error={views.isError}
            empty="No Feature Views yet. Create one above to save time-based values." retry={() => void views.refetch()}>
            {(views.data ?? []).map((item) => <li key={item.name}
              className="flex flex-wrap items-center justify-between gap-2 py-3">
              <span><b>{item.name}</b><p className="text-sm text-muted-foreground">
                {item.status} · v{item.active_version ?? "—"}</p></span>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" disabled={busy} onClick={() => void run(async () => {
                  const result = await api.post<{ version: number }>(
                    `/features/views/${encodeURIComponent(item.name)}/refresh`,
                  );
                  setReadyFeature({ name: item.name, version: result.version });
                }, "Feature version materialized")}>Refresh</Button>
                {readyFeature?.name === item.name && <Button variant="outline" disabled={busy}
                  onClick={() => void run(async () => {
                    await api.post(
                      `/features/views/${encodeURIComponent(item.name)}/versions/${readyFeature.version}/activate`,
                    );
                    setReadyFeature(null);
                  }, "Feature version activated")}>Activate v{readyFeature.version}</Button>}
              </div>
            </li>)}
          </ObjectList>
          <div className="space-y-3 rounded-lg border p-4">
            <h2 className="font-medium">Create a Feature Group</h2>
            <p className="text-sm text-muted-foreground">Choose an entity and one of its active Feature Views. Activate a refreshed view before adding it to a group.</p>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="space-y-1 text-sm">Entity for group
                <select aria-label="Entity for group" value={selectedEntity}
                  onChange={(event) => { setSelectedEntity(event.target.value); setSelectedFeatureView(""); }}
                  className="flex h-9 w-full rounded-md border bg-background px-3 text-sm">
                  <option value="">Choose an entity</option>
                  {(entities.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
              <Field label="Feature Group name" value={query} onChange={setQuery} placeholder="customer_features" />
              <label className="space-y-1 text-sm">Active Feature View
                <select aria-label="Active Feature View" value={selectedFeatureView}
                  onChange={(event) => setSelectedFeatureView(event.target.value)}
                  className="flex h-9 w-full rounded-md border bg-background px-3 text-sm">
                  <option value="">Choose a Feature View</option>
                  {(views.data ?? []).filter((item) => item.entity_id === entity?.id && item.active_version)
                    .map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
                </select>
              </label>
            </div>
            <Button disabled={busy || !query.trim() || !entity || !selectedFeatureView ||
              !(views.data ?? []).some((item) => item.name === selectedFeatureView &&
                item.entity_id === entity.id && item.active_version)}
              onClick={() => void run(() => api.post("/features/groups", {
              name: query, entity_id: entity?.id,
              members: (views.data ?? []).filter((item) =>
                item.name === selectedFeatureView && item.active_version,
              ).map((item) => ({ view_name: item.name, version: item.active_version })),
            }), "Feature Group created")}>Create Feature Group</Button>
          </div>
          <h2 className="font-medium">Feature Groups</h2>
          <ObjectList loading={groups.isPending} error={groups.isError}
            empty="No Feature Groups yet. Activate a Feature View, then create a group above." retry={() => void groups.refetch()}>
            {(groups.data ?? []).map((item) => <li key={item.name} className="py-3">
               <button onClick={() => { setSelectedGroup(item.name); setLookupValues({}); }} className="text-left">
                <b>{item.name}</b><p className="text-sm text-muted-foreground">
                  {item.status} · v{item.active_version ?? "—"}</p>
              </button>
            </li>)}
          </ObjectList>
          {selectedGroup && <div className="space-y-3 rounded-lg border p-4">
            <h2 className="font-medium">Lookup {selectedGroup}</h2>
            <p className="text-sm text-muted-foreground">Enter every identifier for one record to see its stored feature values.</p>
             {groupEntity?.key_columns.map((column) =>
               <Field key={column} label={column} value={lookupValues[column] ?? ""}
                 onChange={(value) => setLookupValues((current) => ({ ...current, [column]: value }))} />)}
             {!groupEntity && <p className="text-sm text-destructive">The group entity is unavailable.</p>}
             <Button disabled={busy || !groupEntity || groupEntity.key_columns.some((column) =>
               !lookupValues[column]?.trim())} onClick={() => void run(async () => {
               const result = await api.post(
                 `/features/groups/${encodeURIComponent(selectedGroup)}/lookup`,
                 { entity_key: Object.fromEntries(groupEntity!.key_columns.map((column) =>
                   [column, lookupValues[column].trim()])) },
              );
              setFeatureResult(result);
            }, "Feature lookup complete")}>Lookup</Button>
            {featureResult !== null && <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
              {JSON.stringify(featureResult, null, 2)}</pre>}
            <div className="border-t pt-4">
              <h3 className="font-medium">Train a model</h3>
              <p className="text-sm text-muted-foreground">Choose a table with known outcomes. The timestamp and target let Nova match past feature values to each outcome.</p>
            </div>
            <div className="grid gap-3 border-t pt-4 md:grid-cols-2">
             <RelationField label="Label relation" value={labelRelation}
               onChange={(next) => {
                 setLabelRelation(next); setLabelTimestamp(""); setTargetColumn("");
               }} />
             <ColumnField label="Label timestamp column" value={labelTimestamp}
               onChange={setLabelTimestamp} options={labelColumnNames} />
             <ColumnField label="Target column" value={targetColumn}
               onChange={setTargetColumn} options={labelColumnNames.filter((column) =>
                 column !== labelTimestamp && !entity?.key_columns.includes(column))} />
              <Field label="Model name" value={modelName} onChange={setModelName} />
              <label className="space-y-1 text-sm">Model type
                <select aria-label="Model type" value={modelType}
                  onChange={(event) => setModelType(event.target.value as typeof modelType)}
                  className="flex h-9 w-full rounded-md border bg-background px-3 text-sm">
                  <option value="classification">Classification</option>
                  <option value="regression">Regression</option>
                </select>
              </label>
              <div className="flex items-end">
                <Button disabled={busy || !labelRelation || !labelTimestamp || !targetColumn ||
                  !modelName || !(groups.data ?? []).find((item) => item.name === selectedGroup)}
                  onClick={() => void run(async () => {
                    const group = (groups.data ?? []).find((item) => item.name === selectedGroup);
                    const keys = (entities.data ?? []).find((item) => item.id === group?.entity_id)?.key_columns;
                    if (!keys) throw new Error("The group entity is unavailable");
                    await api.post(`/features/groups/${encodeURIComponent(selectedGroup)}/train`, {
                      label_relation: labelRelation, entity_keys: keys,
                      event_timestamp: labelTimestamp, label_columns: [targetColumn],
                      model_name: modelName, model_type: modelType, target_column: targetColumn,
                    });
                  }, "Model trained from Feature Group")}>Train model</Button>
              </div>
            </div>
          </div>}
        </section>}
      </div>
      </Main>
    </>
  );
}

function Field({ label, value, onChange, placeholder }: {
  label: string; value: string; onChange: (value: string) => void; placeholder?: string;
}) {
  return <label className="block space-y-1 text-sm">{label}
    <Input value={value} placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)} />
  </label>;
}

function ObjectList({ loading, error, empty, loadingLabel = "Loading list", errorLabel = "Could not load this list.", retry, children }: {
  loading: boolean; error: boolean; empty: string; loadingLabel?: string; errorLabel?: string; retry: () => void;
  children: React.ReactNode;
}) {
  if (loading) return <LoadingOverlay label={loadingLabel} />;
  if (error) return <div role="alert" className="text-sm">{errorLabel}
    <Button variant="link" onClick={retry}>Retry</Button></div>;
  if (!children || (Array.isArray(children) && children.length === 0)) {
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }
  return <ul className="divide-y rounded-lg border px-4">{children}</ul>;
}
