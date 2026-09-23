import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { metadataApi } from "@/features/agents/metadata-api";
import { ColumnField, RelationField } from "./metadata-fields";
import { useRelationColumns } from "./use-relation-columns";

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
type SemanticView = {
  id: string;
  name: string;
  database_name: string;
  active_version: number | null;
  status: string;
};
type SemanticDefinition = {
  metrics?: { name: string; expression?: string | object }[];
  named_filters?: { name: string }[];
  datasets?: { name: string; fields?: {
    name: string; expression?: string | object; kind?: string;
    dimension?: unknown; datatype?: string;
  }[] }[];
};
type SemanticVersion = {
  version: number;
  status: string;
  definition: SemanticDefinition;
  validation: {
    valid: boolean;
    errors: string[];
    warnings: string[];
    regression?: {
      changed: number;
      cases: { verified_query_id: string; question: string; status: string }[];
    } | null;
  } | null;
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

export function IntelligencePage({ initialTab = "features" }: {
  initialTab?: "entities" | "search" | "semantic" | "features";
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<"entities" | "search" | "semantic" | "features">(initialTab);
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
  const [selectedSemantic, setSelectedSemantic] = useState("");
  const [semanticDraft, setSemanticDraft] = useState("");
  const [semanticVersion, setSemanticVersion] = useState("");
  const [semanticMetrics, setSemanticMetrics] = useState("");
  const [semanticDimensions, setSemanticDimensions] = useState("");
  const [semanticNamedFilters, setSemanticNamedFilters] = useState("");
  const [acknowledgeRegressions, setAcknowledgeRegressions] = useState(false);
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
  });
  const indexes = useQuery({
    queryKey: ["intelligence", "search"],
    queryFn: () => api.get<SearchIndex[]>("/ai/search"),
  });
  const semantic = useQuery({
    queryKey: ["intelligence", "semantic"],
    queryFn: () => api.get<SemanticView[]>("/semantic-views"),
  });
  const views = useQuery({
    queryKey: ["intelligence", "feature-views"],
    queryFn: () => api.get<FeatureView[]>("/features/views"),
  });
  const groups = useQuery({
    queryKey: ["intelligence", "feature-groups"],
    queryFn: () => api.get<FeatureGroup[]>("/features/groups"),
  });
  const searchDetail = useQuery({
    queryKey: ["intelligence", "search", selectedSearch],
    queryFn: () => api.get<Detail>(`/ai/search/${encodeURIComponent(selectedSearch)}`),
    enabled: Boolean(selectedSearch),
  });
  const semanticDetail = useQuery({
    queryKey: ["intelligence", "semantic", selectedSemantic],
    queryFn: () => api.get<SemanticView & { versions: SemanticVersion[] }>(
      `/semantic-views/${encodeURIComponent(selectedSemantic)}`,
    ),
    enabled: Boolean(selectedSemantic),
  });
  const databases = useQuery({
    queryKey: ["intelligence", "databases"],
    queryFn: () => metadataApi.listDatabases(true),
  });
  const embeddingModels = useQuery({
    queryKey: ["intelligence", "embedding-models"],
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
  const semanticMetricNames = currentSemanticVersion?.definition.metrics?.map((item) => item.name) ?? [];
  const semanticNamedFilterNames = currentSemanticVersion?.definition.named_filters?.map((item) => item.name) ?? [];
  const semanticDimensionNames = currentSemanticVersion?.definition.datasets?.flatMap((dataset) =>
    (dataset.fields ?? []).filter((field) => field.kind === "dimension" ||
      field.dimension || ["Date", "Time", "DateTime", "DateTimeTz"].includes(field.datatype ?? ""))
      .map((field) => `${dataset.name}.${field.name}`)) ?? [];
  const pageTitle = tab === "features" ? "Feature Store" : tab === "semantic"
    ? "Semantic Views" : tab === "search" ? "AI Search" : "Entities";

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto px-5 py-6 md:px-10 md:py-8">
      <div className="mx-auto w-full max-w-5xl space-y-7">
        <div>
           <h1 className="text-2xl font-heading">{pageTitle}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage business identities, search indexes, semantic views, and features.
          </p>
        </div>
        <nav aria-label="Intelligence sections" className="flex flex-wrap gap-2 border-b pb-4">
          {(["entities", "search", "semantic", "features"] as const).map((item) => (
            <Button key={item} variant={tab === item ? "secondary" : "ghost"}
              aria-current={tab === item ? "page" : undefined}
              onClick={() => setTab(item)} className="capitalize">
              {item === "semantic" ? "Semantic Views" : item === "features" ? "Feature Store" : item}
            </Button>
          ))}
        </nav>

        {tab === "entities" && (
          <section className="space-y-5" aria-label="Entities">
            <div className="grid gap-3 rounded-lg border p-4 md:grid-cols-2">
              <Field label="Entity name" value={name} onChange={setName} />
              <RelationField label="Source relation" value={relation}
                onChange={(next) => { setRelation(next); setKeys("") }} />
              <ColumnField label="Key columns" value={keys} onChange={setKeys}
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
            <ObjectList loading={entities.isPending} error={entities.isError}
              empty="No entities available" retry={() => void entities.refetch()}>
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
            <div className="grid gap-3 rounded-lg border p-4 md:grid-cols-2">
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
            <ObjectList loading={indexes.isPending} error={indexes.isError}
              empty="No search indexes available" retry={() => void indexes.refetch()}>
              {(indexes.data ?? []).map((item) => (
                <li key={item.name} className="flex flex-wrap items-center justify-between gap-2 py-3">
                  <button className="text-left" onClick={() => setSelectedSearch(item.name)}>
                    <b>{item.name}</b><p className="text-sm text-muted-foreground">
                      {item.source_relation} · {item.status} · v{item.active_version ?? "—"}</p>
                  </button>
                  <div className="flex gap-2">
                    <Button variant="outline" disabled={busy} onClick={() => void run(
                      () => api.post(`/ai/search/${encodeURIComponent(item.name)}/rebuild`, {}),
                      "Rebuild queued")}>Rebuild</Button>
                    <Button variant="ghost" disabled={busy} onClick={() => void run(
                      () => api.delete(`/ai/search/${encodeURIComponent(item.name)}`),
                      "Search index deleted")}>Delete</Button>
                  </div>
                </li>
              ))}
            </ObjectList>
            {selectedSearch && <div className="space-y-3 rounded-lg border p-4">
              <h2 className="font-medium">Query {selectedSearch}</h2>
              <div className="flex flex-wrap gap-2">
                <Input aria-label="Search query" className="min-w-40 flex-1" value={query}
                  onChange={(event) => setQuery(event.target.value)} />
                <select aria-label="Search mode" value={mode}
                  onChange={(event) => setMode(event.target.value as typeof mode)}
                  className="rounded-md border bg-background px-3 text-sm">
                  <option>HYBRID</option><option>LEXICAL</option><option>SEMANTIC</option>
                </select>
                <Button disabled={busy || !query} onClick={() => void run(async () => {
                  const result = await api.post<SearchResult>(
                    `/ai/search/${encodeURIComponent(selectedSearch)}/query`,
                    { query, mode },
                  );
                  setSearchResult(result);
                }, "Search complete")}>Search</Button>
              </div>
              {searchResult && <ul className="divide-y" aria-label="Search results">
                {searchResult.hits.map((hit) => <li key={hit.source_key} className="py-3">
                  <p className="text-sm">{hit.content}</p>
                  <p className="text-xs text-muted-foreground">{hit.source_key}</p>
                </li>)}
              </ul>}
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
          </section>
        )}

        {tab === "semantic" && <section className="space-y-5" aria-label="Semantic Views">
          <div className="grid gap-3 rounded-lg border p-4 md:grid-cols-2">
            <Field label="View name" value={name} onChange={setName} />
             <div className="space-y-1 text-sm"><span>Database</span>
               <SearchableSelect label="Semantic View database" options={databases.data ?? []}
                 value={semanticDatabase} onChange={setSemanticDatabase}
                 allowEmpty={false} emptyLabel="Choose database"
                 className="h-9 w-full justify-start" />
             </div>
            <label className="space-y-1 text-sm md:col-span-2">Ossie definition
              <Textarea rows={9} value={definition}
                onChange={(event) => setDefinition(event.target.value)} />
            </label>
             <Button disabled={busy || !name || !semanticDatabase || !definition}
               onClick={() => void run(() => api.post("/semantic-views", {
                 name, database: semanticDatabase, definition,
              }), "Semantic View created")}>Create draft</Button>
          </div>
           <ObjectList loading={semantic.isPending} error={semantic.isError}
             empty="No Semantic Views available" retry={() => void semantic.refetch()}>
             {(semantic.data ?? []).map((item) => <li key={item.id} className="py-3">
               <button className="text-left" onClick={() => {
                 setSelectedSemantic(item.id); setSemanticDraft(""); setSemanticVersion("");
                 setSemanticMetrics(""); setSemanticDimensions(""); setSemanticNamedFilters("");
               }}>
                <b>{item.name}</b><p className="text-sm text-muted-foreground">
                  {item.database_name} · {item.status} · v{item.active_version ?? "—"}</p>
              </button>
            </li>)}
          </ObjectList>
           {selectedSemantic && <div className="space-y-3 rounded-lg border p-4">
             <div className="flex flex-wrap items-center justify-between gap-2">
               <h2 className="font-medium">Semantic View versions</h2>
               <div className="flex gap-2">
                 <Button variant="outline" disabled={busy} onClick={() => void run(
                   () => api.post(`/semantic-views/${encodeURIComponent(selectedSemantic)}/deprecate`, {}),
                   "Semantic View deprecated")}>Deprecate</Button>
                 <Button variant="destructive" disabled={busy}
                   onClick={() => setConfirmSemanticDrop(true)}>Delete</Button>
               </div>
             </div>
             {(semanticDetail.data?.versions ?? []).map((version) =>
               <div key={version.version} className="space-y-2 border-t pt-3 text-sm">
                 <div className="flex flex-wrap items-center gap-2">
                   <span className="mr-auto">v{version.version} · {version.status}</span>
                   <Button variant="ghost" onClick={() => setSemanticDraft(
                     editableSemanticDefinition(version.definition))}>Use as draft</Button>
                   {version.status === "DRAFT" && <Button variant="outline" disabled={busy}
                     onClick={() => void run(() => api.post(
                       `/semantic-views/${encodeURIComponent(selectedSemantic)}/versions/${version.version}/validate`,
                     ), "Validation complete")}>Validate</Button>}
                   {version.status === "VALIDATED" && <Button variant="outline" disabled={busy}
                     onClick={() => void run(() => api.post(
                       `/semantic-views/${encodeURIComponent(selectedSemantic)}/versions/${version.version}/publish`,
                       { acknowledge_regressions: acknowledgeRegressions },
                     ), "Version published")}>Publish</Button>}
                 </div>
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
             <label className="block space-y-1 text-sm">New version definition (Ossie YAML or JSON)
               <Textarea rows={9} value={semanticDraft}
                 onChange={(event) => setSemanticDraft(event.target.value)} />
             </label>
             <Button disabled={busy || !semanticDraft.trim()} onClick={() => void run(
               () => api.post(`/semantic-views/${encodeURIComponent(selectedSemantic)}/versions`,
                 { definition: semanticDraft }), "Draft version created")}>Add version</Button>
             <div className="grid gap-3 border-t pt-4 md:grid-cols-2">
               <ColumnField label="Metrics" value={semanticMetrics} onChange={setSemanticMetrics}
                 options={semanticMetricNames} multiple />
               <ColumnField label="Dimensions" value={semanticDimensions} onChange={setSemanticDimensions}
                 options={semanticDimensionNames} multiple />
               <ColumnField label="Named filters" value={semanticNamedFilters}
                 onChange={setSemanticNamedFilters} options={semanticNamedFilterNames} multiple />
               <div className="space-y-1 text-sm"><span>Version</span>
                 <select aria-label="Semantic query version" value={semanticVersion}
                   onChange={(event) => setSemanticVersion(event.target.value)}
                   className="flex h-9 w-full rounded-md border bg-background px-3 text-sm">
                   <option value="">Active version</option>
                   {(semanticDetail.data?.versions ?? []).filter((item) =>
                     item.status === "ACTIVE" || item.status === "DEPRECATED")
                     .map((item) => <option key={item.version} value={item.version}>v{item.version}</option>)}
                 </select>
               </div>
             </div>
             <Button disabled={busy || !semanticMetrics && !semanticDimensions}
               onClick={() => void run(async () => {
               const result = await api.post(`/semantic-views/${encodeURIComponent(selectedSemantic)}/query`,
                 { metrics: splitColumns(semanticMetrics), dimensions: splitColumns(semanticDimensions),
                   named_filters: splitColumns(semanticNamedFilters),
                   version: semanticVersion ? Number(semanticVersion) : null });
               setSemanticResult(result);
             }, "Semantic query complete")}>Run query</Button>
             {semanticResult !== null && <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
               {JSON.stringify(semanticResult, null, 2)}</pre>}
             <ConfirmDialog open={confirmSemanticDrop} onOpenChange={setConfirmSemanticDrop}
               title="Delete Semantic View"
               desc="This removes every version of this Semantic View."
               confirmText="Delete" destructive isLoading={busy}
               handleConfirm={() => {
                 setConfirmSemanticDrop(false);
                 void run(async () => {
                   await api.delete(`/semantic-views/${encodeURIComponent(selectedSemantic)}`);
                   setSelectedSemantic("");
                 }, "Semantic View deleted");
               }} />
           </div>}
        </section>}

        {tab === "features" && <section className="space-y-5" aria-label="Feature Store">
          <div className="grid gap-3 rounded-lg border p-4 md:grid-cols-2">
            <Field label="Feature View name" value={name} onChange={setName} />
            <label className="space-y-1 text-sm">Entity
              <select aria-label="Entity" value={selectedEntity}
                onChange={(event) => setSelectedEntity(event.target.value)}
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
          <ObjectList loading={views.isPending} error={views.isError}
            empty="No Feature Views available" retry={() => void views.refetch()}>
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
          <div className="flex flex-wrap gap-2 rounded-lg border p-4">
            <Input aria-label="Feature Group name" className="min-w-40 flex-1"
              placeholder="Feature Group name" value={query}
              onChange={(event) => setQuery(event.target.value)} />
            <select aria-label="Active Feature View" value={selectedFeatureView}
              onChange={(event) => setSelectedFeatureView(event.target.value)}
              className="rounded-md border bg-background px-3 text-sm">
              <option value="">Choose a Feature View</option>
              {(views.data ?? []).filter((item) => item.entity_id === entity?.id && item.active_version)
                .map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
            </select>
            <Button disabled={busy || !query || !entity || !selectedFeatureView}
              onClick={() => void run(() => api.post("/features/groups", {
              name: query, entity_id: entity?.id,
              members: (views.data ?? []).filter((item) =>
                item.name === selectedFeatureView && item.active_version,
              ).map((item) => ({ view_name: item.name, version: item.active_version })),
            }), "Feature Group created")}>Create Feature Group</Button>
          </div>
          <ObjectList loading={groups.isPending} error={groups.isError}
            empty="No Feature Groups available" retry={() => void groups.refetch()}>
            {(groups.data ?? []).map((item) => <li key={item.name} className="py-3">
               <button onClick={() => { setSelectedGroup(item.name); setLookupValues({}); }} className="text-left">
                <b>{item.name}</b><p className="text-sm text-muted-foreground">
                  {item.status} · v{item.active_version ?? "—"}</p>
              </button>
            </li>)}
          </ObjectList>
          {selectedGroup && <div className="space-y-3 rounded-lg border p-4">
            <h2 className="font-medium">Lookup {selectedGroup}</h2>
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
    </main>
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

function ObjectList({ loading, error, empty, retry, children }: {
  loading: boolean; error: boolean; empty: string; retry: () => void;
  children: React.ReactNode;
}) {
  if (loading) return <p role="status" className="text-sm text-muted-foreground">Loading…</p>;
  if (error) return <div role="alert" className="text-sm">Could not load this list.
    <Button variant="link" onClick={retry}>Retry</Button></div>;
  if (!children || (Array.isArray(children) && children.length === 0)) {
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }
  return <ul className="divide-y rounded-lg border px-4">{children}</ul>;
}
