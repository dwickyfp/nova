import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Database, FileCode2, Search, Trash2, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/layout/header";
import { Main } from "@/components/layout/main";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { agentsApi, type SemanticModel } from "./api";
import { SemanticInspector } from "./semantic-inspector";

const SAMPLE = `version: 0.1.1
name: my_model
description: Business semantic model
datasets:
  - name: orders
    source: db.schema.orders
    fields:
      - name: order_date
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: order_date
        datatype: Date
        dimension:
          is_time: true
metrics:
  - name: total_revenue
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: SUM(orders.amount)
`;

export function SemanticModelsPage() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [definition, setDefinition] = useState(SAMPLE);
  const [inspectedModel, setInspectedModel] = useState<SemanticModel | null>(
    null,
  );

  const modelsQuery = useQuery({
    queryKey: ["agents", "semantic-models"],
    queryFn: () => agentsApi.listSemanticModels(),
  });

  const validate = useMutation({
    mutationFn: () => agentsApi.validateSemanticModel(definition),
  });

  const create = useMutation({
    mutationFn: () =>
      agentsApi.createSemanticModel({
        name: name.trim(),
        description: description.trim(),
        definition,
      }),
    onSuccess: (model) => {
      toast.success(`Semantic model "${model.name}" created`);
      setOpen(false);
      setName("");
      setDescription("");
      queryClient.invalidateQueries({
        queryKey: ["agents", "semantic-models"],
      });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => agentsApi.deleteSemanticModel(id),
    onSuccess: () => {
      toast.success("Semantic model deleted");
      queryClient.invalidateQueries({
        queryKey: ["agents", "semantic-models"],
      });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const validation = validate.data;

  return (
    <>
      <Header fixed />
      <Main>
        <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">
              Semantic models
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Define metrics, dimensions, and relationships once. Agents answer
              business questions from these definitions.
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button variant="outline" asChild>
              <Link to="/agents/semantic/builder">
                <Wand2 className="size-4" />
                Build visually
              </Link>
            </Button>
            <Button onClick={() => setOpen(true)}>
              <FileCode2 className="size-4" />
              Import YAML
            </Button>
          </div>
        </div>

        <ScrollArea className="min-h-0 flex-1">
          {modelsQuery.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : (modelsQuery.data?.models.length ?? 0) === 0 ? (
            <EmptyState
              icon={FileCode2}
              title="No semantic models"
              description="Build one visually from your tables, or import an Ossie document."
              action={
                <Button asChild>
                  <Link to="/agents/semantic/builder">
                    <Wand2 className="size-4" />
                    Build visually
                  </Link>
                </Button>
              }
            />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {modelsQuery.data?.models.map((model) => (
                <div
                  key={model.semantic_model_id}
                  className="flex items-start justify-between rounded-lg border p-4"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{model.name}</span>
                      <Badge variant="outline">
                        Ossie {model.ossie_version}
                      </Badge>
                    </div>
                    {model.description ? (
                      <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                        {model.description}
                      </p>
                    ) : null}
                    <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        <Database className="size-3" />
                        {model.database_name || "—"}
                      </span>
                      <span>
                        {Array.isArray(model.definition.datasets)
                          ? model.definition.datasets.length
                          : 0}{" "}
                        datasets
                      </span>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setInspectedModel(model)}
                    >
                      <Search className="size-4" />
                      Inspect
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => remove.mutate(model.semantic_model_id)}
                      aria-label={`Delete ${model.name}`}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </Main>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Import Ossie YAML</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="sm-name">Name</Label>
                <Input
                  id="sm-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="sales_analytics"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="sm-desc">Description</Label>
                <Input
                  id="sm-desc"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Sales and customer analytics"
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="sm-def">Ossie definition (YAML)</Label>
              <Textarea
                id="sm-def"
                value={definition}
                onChange={(e) => setDefinition(e.target.value)}
                className="min-h-64 font-mono text-xs"
              />
            </div>
            {validation ? (
              <div className="rounded-md border p-3 text-sm">
                {validation.valid ? (
                  <p className="text-success">
                    Valid Ossie {validation.ossie_version}:{" "}
                    {validation.dataset_count} datasets,{" "}
                    {validation.metric_count} metrics,{" "}
                    {validation.relationship_count} relationships.
                  </p>
                ) : (
                  <div className="space-y-1">
                    {validation.errors.map((error, i) => (
                      <p key={i} className="text-destructive">
                        {error}
                      </p>
                    ))}
                  </div>
                )}
                {validation.warnings.map((warning, i) => (
                  <p key={i} className="text-warning-strong">
                    {warning}
                  </p>
                ))}
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => validate.mutate()}>
              Validate
            </Button>
            <Button
              disabled={!name.trim() || create.isPending}
              onClick={() => create.mutate()}
            >
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <SemanticInspector
        model={inspectedModel}
        onOpenChange={(next) => {
          if (!next) setInspectedModel(null);
        }}
      />
    </>
  );
}
