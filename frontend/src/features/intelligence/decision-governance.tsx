import { useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingLines } from "@/components/ui/loading-overlay";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api-client";

type Share = {
  share_id: string;
  target_name: string;
  target_type: "user" | "role";
};

export function DecisionShares({
  decisionId,
  epoch,
}: {
  decisionId: string;
  epoch: number;
}) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState<"user" | "role">("user");
  const path = `/intelligence/decisions/${encodeURIComponent(decisionId)}/shares`;
  const key = ["intelligence", epoch, "shares", decisionId];
  const shares = useQuery({
    queryKey: key,
    queryFn: () => api.get<{ items: Share[] }>(path),
    enabled: open,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
  const mutation = useMutation({
    mutationFn: (shareId?: string) =>
      shareId
        ? api.delete(`${path}/${encodeURIComponent(shareId)}`)
        : api.post(path, {
            object_type: "decision",
            object_id: decisionId,
            target_type: type,
            target_name: name.trim(),
          }),
    onSuccess: () => {
      setName("");
      void client.invalidateQueries({ queryKey: key });
    },
  });
  return (
    <section className="space-y-3 rounded-md border p-3">
      <Button
        variant="outline"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        Manage decision sharing
      </Button>
      {open && (
        <>
          <p className="text-sm text-muted-foreground">
            Reviewers use their own data permissions. Approval also requires the
            configured reviewer role.
          </p>
          <form
            className="flex flex-col gap-3 sm:flex-row sm:items-end"
            onSubmit={(event) => {
              event.preventDefault();
              mutation.mutate(undefined);
            }}
          >
            <div className="space-y-1">
              <Label htmlFor="decision-share-kind">Share with</Label>
              <Select
                value={type}
                onValueChange={(value) => setType(value as "user" | "role")}
              >
                <SelectTrigger id="decision-share-kind">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="user">User</SelectItem>
                  <SelectItem value="role">Active role</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="min-w-0 flex-1 space-y-1">
              <Label htmlFor="decision-share-name">Name</Label>
              <Input
                id="decision-share-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
                maxLength={128}
                pattern="[A-Za-z_][A-Za-z0-9_.-]*"
              />
            </div>
            <Button type="submit" disabled={!name.trim() || mutation.isPending}>
              Share decision
            </Button>
          </form>
          {(shares.isError || mutation.isError) && (
            <p role="alert" className="text-sm text-destructive">
              Sharing could not be updated or loaded.{" "}
              <Button
                variant="link"
                onClick={() => {
                  mutation.reset();
                  void shares.refetch();
                }}
              >
                Reload sharing
              </Button>
            </p>
          )}
          {shares.isFetching ? (
            <LoadingLines />
          ) : (
            !shares.isError &&
            shares.data && (
              <ul className="divide-y">
                {shares.data.items.map((share) => (
                  <li
                    key={share.share_id}
                    className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
                  >
                    <span className="break-all">
                      {share.target_name} · {share.target_type}
                    </span>
                    <Button
                      variant="outline"
                      disabled={mutation.isPending}
                      aria-label={`Remove access for ${share.target_name}`}
                      onClick={() => mutation.mutate(share.share_id)}
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            )
          )}
          {!shares.isFetching &&
            !shares.isError &&
            shares.data?.items.length === 0 && (
              <p className="text-sm text-muted-foreground">
                This decision has no shares.
              </p>
            )}
        </>
      )}
    </section>
  );
}

type Measurements = Record<
  string,
  { mean: number | null; available_outcomes: number }
>;

type Effectiveness = {
  dimensions: Measurements;
  metric_groups?: {
    semantic: { view_id: string; version: number; fingerprint: string };
    metric: string;
    currency: string;
    dimensions: Measurements;
  }[];
  next_after: string | null;
};

function OutcomeMeasurements({ dimensions }: { dimensions: Measurements }) {
  return (
    <dl className="grid gap-3 text-sm sm:grid-cols-2">
      {Object.entries(dimensions).map(([dimension, value]) => (
        <div key={dimension}>
          <dt className="text-muted-foreground">
            {dimension.split("_").join(" ")}
          </dt>
          <dd>
            {value.mean == null
              ? "Unavailable"
              : new Intl.NumberFormat(undefined, {
                  maximumFractionDigits: 3,
                }).format(value.mean)}{" "}
            · {value.available_outcomes} measured outcomes
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function EffectivenessPanel({ epoch }: { epoch: number }) {
  const [open, setOpen] = useState(false);
  const report = useInfiniteQuery({
    queryKey: ["intelligence", epoch, "effectiveness"],
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      api.get<Effectiveness>(
        `/intelligence/effectiveness?after=${encodeURIComponent(pageParam)}`,
      ),
    getNextPageParam: (last) => last.next_after || undefined,
    enabled: open,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
  return (
    <section className="space-y-3">
      <Button
        variant="outline"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        Decision effectiveness
      </Button>
      {open && (
        <>
          <p className="text-sm text-muted-foreground">
            Each page summarizes outcomes you can currently access. Unavailable
            measurements are excluded from its averages. Monetary results are
            grouped by metric, currency, and published definition.
          </p>
          {report.isError ? (
            <p role="alert" className="text-sm text-destructive">
              Effectiveness is unavailable.{" "}
              <Button variant="link" onClick={() => void report.refetch()}>
                Retry
              </Button>
            </p>
          ) : report.isFetching && !report.isFetchingNextPage ? (
            <LoadingLines />
          ) : (
            report.data?.pages.map((result, index) => (
              <section
                key={index}
                className="space-y-2 rounded-md border p-3"
                aria-label={`Outcome measurements page ${index + 1}`}
              >
                <h3 className="text-sm font-medium">Page {index + 1}</h3>
                <OutcomeMeasurements dimensions={result.dimensions} />
                {result.metric_groups?.map((group) => (
                  <section
                    key={`${group.semantic.view_id}:${group.semantic.fingerprint}:${group.metric}:${group.currency}`}
                    className="space-y-2 border-t pt-3"
                  >
                    <h4 className="break-words text-sm font-medium">
                      {group.metric} · {group.currency} · Definition{" "}
                      {group.semantic.version}
                    </h4>
                    <OutcomeMeasurements dimensions={group.dimensions} />
                  </section>
                ))}
              </section>
            ))
          )}
          {report.hasNextPage && (
            <Button
              variant="outline"
              disabled={report.isFetchingNextPage}
              onClick={() => void report.fetchNextPage()}
            >
              Load more outcomes
            </Button>
          )}
        </>
      )}
    </section>
  );
}
