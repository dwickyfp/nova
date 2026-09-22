import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  DatabaseZap,
  Play,
  Save,
  ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  agentsApi,
  type SemanticLintResult,
  type SemanticModel,
  type SemanticPreview,
} from "./api";

export function SemanticInspector({
  model,
  onOpenChange,
}: {
  model: SemanticModel | null;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");
  const [preview, setPreview] = useState<SemanticPreview | null>(null);

  useEffect(() => {
    setQuestion("");
    setPreview(null);
  }, [model?.semantic_model_id]);

  const lintQuery = useQuery({
    queryKey: ["agents", "semantic-models", model?.semantic_model_id, "lint"],
    queryFn: () => agentsApi.lintSemanticModel(model!.semantic_model_id),
    enabled: Boolean(model),
  });
  const verifiedQuery = useQuery({
    queryKey: [
      "agents",
      "semantic-models",
      model?.semantic_model_id,
      "verified-queries",
    ],
    queryFn: () => agentsApi.listVerifiedQueries(model!.semantic_model_id),
    enabled: Boolean(model),
  });
  const runPreview = useMutation({
    mutationFn: () =>
      agentsApi.previewSemanticQuestion(
        model!.semantic_model_id,
        question.trim(),
      ),
    onSuccess: setPreview,
    onError: (error: Error) => toast.error(error.message),
  });
  const saveVerified = useMutation({
    mutationFn: () => {
      if (!model || !preview) throw new Error("Run a semantic preview first");
      return agentsApi.createVerifiedQuery(model.semantic_model_id, {
        question: question.trim(),
        semantic_plan: preview.semantic_plan,
        verified_sql: preview.generated_sql,
      });
    },
    onSuccess: () => {
      toast.success("Verified query saved");
      queryClient.invalidateQueries({
        queryKey: [
          "agents",
          "semantic-models",
          model?.semantic_model_id,
          "verified-queries",
        ],
      });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <Dialog open={Boolean(model)} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-svh max-w-5xl overflow-y-auto">
        <DialogHeader>
          <div className="flex flex-wrap items-center gap-2">
            <DialogTitle>{model?.name ?? "Semantic model"}</DialogTitle>
            {model ? (
              <Badge variant="outline">Ossie {model.ossie_version}</Badge>
            ) : null}
          </div>
          <DialogDescription>
            Inspect how Nova understands, validates, and reuses this governed
            model.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="preview" className="min-h-0">
          <TabsList>
            <TabsTrigger value="preview">Query preview</TabsTrigger>
            <TabsTrigger value="quality">Quality</TabsTrigger>
            <TabsTrigger value="verified">
              Verified queries
              {verifiedQuery.data?.count ? (
                <Badge variant="secondary" className="ml-1.5">
                  {verifiedQuery.data.count}
                </Badge>
              ) : null}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="preview" className="mt-5 space-y-5">
            <form
              className="flex flex-col gap-2 sm:flex-row"
              onSubmit={(event) => {
                event.preventDefault();
                if (question.trim()) runPreview.mutate();
              }}
            >
              <Input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Revenue by region last month"
                aria-label="Semantic question"
              />
              <Button
                type="submit"
                disabled={!question.trim() || runPreview.isPending}
              >
                <Play className="size-4" />
                Preview
              </Button>
            </form>

            {runPreview.isPending ? (
              <div className="space-y-3">
                <Skeleton className="w-full py-10" />
                <Skeleton className="w-full py-16" />
              </div>
            ) : preview ? (
              <PreviewResult
                preview={preview}
                saving={saveVerified.isPending}
                onSave={() => saveVerified.mutate()}
              />
            ) : (
              <EmptyState
                icon={DatabaseZap}
                title="Test a business question"
                description="Nova will show the selected concepts, relationship path, confidence, and compiled StarRocks SQL without executing the query."
              />
            )}
          </TabsContent>

          <TabsContent value="quality" className="mt-5">
            {lintQuery.isLoading ? (
              <Skeleton className="w-full py-24" />
            ) : lintQuery.data ? (
              <QualityResult result={lintQuery.data} />
            ) : (
              <p className="text-sm text-destructive">
                Could not load semantic model quality.
              </p>
            )}
          </TabsContent>

          <TabsContent value="verified" className="mt-5">
            {verifiedQuery.isLoading ? (
              <Skeleton className="w-full py-20" />
            ) : (verifiedQuery.data?.queries.length ?? 0) === 0 ? (
              <EmptyState
                icon={ShieldCheck}
                title="No verified queries"
                description="Run a query preview, review its plan and SQL, then save it as a known-good example."
              />
            ) : (
              <div className="divide-y rounded-lg border">
                {verifiedQuery.data?.queries.map((query) => (
                  <div
                    key={query.verified_query_id}
                    className="space-y-2 px-4 py-3"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className="text-sm font-medium">{query.question}</p>
                      <span className="text-xs text-muted-foreground">
                        {new Date(query.verified_at).toLocaleString()}
                      </span>
                    </div>
                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-muted/40 p-3 font-mono text-xs">
                      {query.verified_sql}
                    </pre>
                    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                      <span>{query.usage_count.toLocaleString()} uses</span>
                      <span>
                        {query.success_count.toLocaleString()} successful
                      </span>
                      <span>Verified by {query.verified_by}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

function PreviewResult({
  preview,
  saving,
  onSave,
}: {
  preview: SemanticPreview;
  saving: boolean;
  onSave: () => void;
}) {
  const confidence = preview.confidence;
  const score = typeof confidence.score === "number" ? confidence.score : null;
  const level =
    typeof confidence.level === "string" ? confidence.level : "unknown";
  const unresolved = stringList(confidence.unresolved);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3">
        <div>
          <p className="text-xs text-muted-foreground">Semantic confidence</p>
          <div className="mt-1 flex items-center gap-2">
            <Badge variant={level === "high" ? "secondary" : "outline"}>
              {level}
            </Badge>
            <span className="text-sm tabular-nums">
              {score == null
                ? "Score unavailable"
                : `${Math.round(score * 100)}%`}
            </span>
          </div>
        </div>
        <Button variant="outline" disabled={saving} onClick={onSave}>
          <Save className="size-4" />
          Save verified query
        </Button>
      </div>

      {preview.relationship_path.length ? (
        <section>
          <h3 className="text-sm font-medium">Relationship path</h3>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {preview.relationship_path.map((dataset, index) => (
              <span key={`${dataset}-${index}`} className="contents">
                {index > 0 ? (
                  <ArrowRight className="size-3.5 text-muted-foreground" />
                ) : null}
                <Badge variant="outline" className="font-mono">
                  {dataset}
                </Badge>
              </span>
            ))}
          </div>
        </section>
      ) : null}

      <section>
        <h3 className="text-sm font-medium">Semantic plan</h3>
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-md bg-muted/40 p-3 font-mono text-xs">
          {JSON.stringify(preview.semantic_plan, null, 2)}
        </pre>
      </section>

      <section>
        <h3 className="text-sm font-medium">Generated StarRocks SQL</h3>
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-md bg-muted/40 p-3 font-mono text-xs">
          {preview.generated_sql}
        </pre>
      </section>

      {preview.warnings.length || unresolved.length ? (
        <section className="rounded-lg border border-warning/40 bg-warning/5 p-3">
          <h3 className="flex items-center gap-2 text-sm font-medium">
            <AlertTriangle className="size-4 text-warning-strong" />
            Review before verification
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
            {[
              ...preview.warnings,
              ...unresolved.map((item) => `Unresolved: ${item}`),
            ].map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function QualityResult({ result }: { result: SemanticLintResult }) {
  const quality = Object.entries(result.quality).filter(
    ([, value]) => typeof value === "number" || typeof value === "string",
  );
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3">
        <div className="flex items-center gap-2">
          {result.valid ? (
            <CheckCircle2 className="size-4 text-success" />
          ) : (
            <AlertTriangle className="size-4 text-destructive" />
          )}
          <span className="text-sm font-medium">
            {result.valid ? "Compiler-ready" : "Validation failed"}
          </span>
        </div>
        <span className="max-w-full truncate font-mono text-xs text-muted-foreground">
          {result.model_fingerprint}
        </span>
      </div>

      {quality.length ? (
        <section>
          <h3 className="text-sm font-medium">Readiness indicators</h3>
          <dl className="mt-2 grid gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-2 lg:grid-cols-3">
            {quality.map(([key, value]) => (
              <div key={key} className="bg-background px-3 py-2.5">
                <dt className="text-xs text-muted-foreground">
                  {humanize(key)}
                </dt>
                <dd className="mt-1 text-sm font-medium tabular-nums">
                  {formatQuality(value)}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {result.errors.length ? (
        <section>
          <h3 className="text-sm font-medium text-destructive">
            Validation errors
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
            {result.errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <section>
        <h3 className="text-sm font-medium">Lint findings</h3>
        {result.findings.length ? (
          <div className="mt-2 divide-y rounded-lg border">
            {result.findings.map((finding, index) => (
              <div
                key={`${finding.code}-${finding.object_name}-${index}`}
                className="px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    variant={
                      finding.severity === "error" ? "destructive" : "outline"
                    }
                  >
                    {finding.severity}
                  </Badge>
                  <span className="font-mono text-xs">{finding.code}</span>
                  {finding.object_name ? (
                    <span className="text-xs text-muted-foreground">
                      {finding.object_name}
                    </span>
                  ) : null}
                </div>
                <p className="mt-1.5 text-sm">{finding.message}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">
            No lint findings.
          </p>
        )}
      </section>
    </div>
  );
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function humanize(value: string) {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter: string) => letter.toUpperCase());
}

function formatQuality(value: unknown) {
  if (typeof value === "number" && value >= 0 && value <= 1)
    return `${Math.round(value * 100)}%`;
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}
