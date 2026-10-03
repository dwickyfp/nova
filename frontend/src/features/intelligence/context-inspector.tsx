import { useId, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingLines } from "@/components/ui/loading-overlay";
import type { SemanticRef, Page } from "./lifecycle-api";

type Node = {
  id: string;
  name: string;
  kind: string;
  reference_id: string;
  state: string;
  authority?: string;
  semantic?: SemanticRef;
};
type Graph = {
  nodes: Node[];
  edges: { id: string; source: string; target: string; relationship: string }[];
  bounded: boolean;
};

export function ContextInspector({
  semantic,
  initialNodeId,
}: {
  semantic?: SemanticRef;
  initialNodeId?: string;
}) {
  const epoch = useAuthStore((s) => s.securityEpoch);
  return (
    <Inspector
      key={`${epoch}:${semantic?.fingerprint ?? ""}:${initialNodeId ?? ""}`}
      semantic={semantic}
      epoch={epoch}
      initialNodeId={initialNodeId}
    />
  );
}

function Inspector({
  semantic,
  epoch,
  initialNodeId,
}: {
  semantic?: SemanticRef;
  epoch: number;
  initialNodeId?: string;
}) {
  const id = useId();
  const [nodeId, setNodeId] = useState(initialNodeId ?? "");
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [after, setAfter] = useState("");
  const project = useMutation({
    mutationFn: () =>
      api.post<{ root_id: string }>(
        "/intelligence/context/project-semantic-view",
        semantic,
      ),
    onSuccess: (value) => setNodeId(value.root_id),
  });
  const graph = useQuery({
    queryKey: ["context-graph", epoch, nodeId],
    queryFn: () =>
      api.get<Graph>(
        `/intelligence/context/${encodeURIComponent(nodeId)}/graph?depth=2&limit=50`,
      ),
    enabled: Boolean(nodeId),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const results = useQuery({
    queryKey: ["context-search", epoch, term, after],
    queryFn: () =>
      api.get<Page<Node>>(
        `/intelligence/context/search?term=${encodeURIComponent(term)}&after=${encodeURIComponent(after)}`,
      ),
    enabled: Boolean(term),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const names = new Map(graph.data?.nodes.map((node) => [node.id, node.name]));
  return (
    <section className="min-w-0 space-y-4" aria-label="Context Graph">
      <p className="text-sm text-muted-foreground">
        Inspect business knowledge, published definitions, and their source
        relationships. Results follow your current data access.
      </p>
      {semantic && (
        <Button
          variant="outline"
          disabled={project.isPending}
          onClick={() => project.mutate()}
        >
          {project.isPending
            ? "Preparing context…"
            : "Inspect this published version"}
        </Button>
      )}
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setTerm(search.trim());
          setAfter("");
        }}
      >
        <div className="min-w-0 flex-1 space-y-2">
          <Label htmlFor={id}>Find a business concept</Label>
          <Input
            id={id}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            maxLength={128}
          />
        </div>
        <Button type="submit" variant="outline" disabled={!search.trim()}>
          Search context
        </Button>
      </form>
      {(project.isError || graph.isError || results.isError) && (
        <p role="alert" className="text-sm text-destructive">
          Context is unavailable or its definition changed. Refresh the Semantic
          View and retry.
        </p>
      )}
      {results.isFetching ? (
        <LoadingLines rows={2} />
      ) : (
        !results.isError &&
        results.data && (
          <div className="space-y-2">
            <ul className="divide-y rounded-md border">
              {results.data.items.map((node) => (
                <li key={node.id}>
                  <Button
                    variant="ghost"
                    className="h-auto w-full justify-start whitespace-normal py-3 text-left"
                    onClick={() => setNodeId(node.id)}
                  >
                    {node.name} · {node.kind}
                  </Button>
                </li>
              ))}
            </ul>
            {!results.data.items.length && (
              <p className="text-sm">No matching concepts are visible.</p>
            )}
            {results.data.next_after && (
              <Button
                variant="outline"
                onClick={() => setAfter(results.data.next_after!)}
              >
                Next results
              </Button>
            )}
          </div>
        )
      )}
      {graph.isFetching ? (
        <LoadingLines />
      ) : (
        !graph.isError &&
        graph.data && (
          <div className="space-y-4">
            <h4 className="font-medium">Concepts and citations</h4>
            <ul className="divide-y rounded-md border">
              {graph.data.nodes.map((node) => (
                <li key={node.id} className="min-w-0 space-y-1 p-3">
                  <Button
                    variant="link"
                    className="h-auto max-w-full whitespace-normal px-0 text-left"
                    onClick={() => setNodeId(node.id)}
                  >
                    {node.name}
                  </Button>
                  <p className="text-xs text-muted-foreground">
                    {node.kind} · {node.state.toLowerCase()} ·{" "}
                    {node.authority?.split("_").join(" ") || "Unverified"}
                  </p>
                  <p className="break-all text-xs">
                    {node.reference_id}
                    {node.semantic &&
                      ` · Semantic version ${node.semantic.version}`}
                  </p>
                </li>
              ))}
            </ul>
            <h4 className="font-medium">Relationships</h4>
            <ul className="space-y-2 text-sm">
              {graph.data.edges.map((edge) => (
                <li className="break-words" key={edge.id}>
                  {names.get(edge.source)} →{" "}
                  {edge.relationship.split("_").join(" ")} →{" "}
                  {names.get(edge.target)}
                </li>
              ))}
            </ul>
            {graph.data.bounded && (
              <p className="text-sm text-muted-foreground">
                This graph is limited by depth and size. Select a concept to
                inspect its neighborhood.
              </p>
            )}
          </div>
        )
      )}
    </section>
  );
}
