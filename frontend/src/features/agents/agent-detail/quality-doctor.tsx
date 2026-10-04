import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { qualityApi, type DoctorReport } from "../quality-api";

export function AgentDoctor({
  agentId,
  epoch,
  onInspectTrace,
}: {
  agentId: string;
  epoch: number;
  onInspectTrace?: (id: string) => void;
}) {
  const query = useQuery({
    queryKey: ["agent-quality", epoch, agentId, "doctor"],
    queryFn: ({ signal }) => qualityApi.doctor(agentId, signal),
    retry: false,
    gcTime: 0,
  });
  return (
    <section className="min-w-0 space-y-3" aria-label="Agent Doctor">
      <h2 className="text-base font-medium">Agent Doctor</h2>
      {query.isPending ? (
        <p role="status" className="text-sm">
          Loading release diagnosis…
        </p>
      ) : query.isError ? (
        <div role="alert" className="space-y-2 text-sm">
          <p>Release diagnosis could not be loaded: {query.error.message}</p>
          <Button
            variant="outline"
            className="min-h-11"
            onClick={() => void query.refetch()}
          >
            Retry diagnosis
          </Button>
        </div>
      ) : (
        <DoctorFindings report={query.data} onInspectTrace={onInspectTrace} />
      )}
    </section>
  );
}

export function DoctorFindings({
  report,
  onInspectTrace,
}: {
  report: DoctorReport;
  onInspectTrace?: (id: string) => void;
}) {
  return (
    <div className="min-w-0 space-y-3 text-sm">
      <dl className="grid min-w-0 gap-3 sm:grid-cols-3">
        {(
          [
            ["Known-good release", report.known_good],
            ["First bad evaluated release", report.first_bad],
            ["Current evaluated release", report.current],
          ] as const
        ).map(([label, release]) => (
          <div key={label} className="min-w-0">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="break-words font-medium">
              {release?.version_id ?? "Not established"}
            </dd>
          </div>
        ))}
      </dl>
      {report.requirements?.map((item) => (
        <p key={`${item.code}:${item.detail}`}>{item.detail}</p>
      ))}
      {!!report.regressions?.length && (
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <caption className="pb-2 text-left text-muted-foreground">
              Regressions on compatible frozen cases and scorers
            </caption>
            <thead>
              <tr>
                <th className="p-2">Case</th>
                <th className="p-2">Scorer</th>
                <th className="p-2">Result</th>
                <th className="p-2">Measured change</th>
              </tr>
            </thead>
            <tbody>
              {report.regressions.map((item) => (
                <tr key={`${item.case_id}:${item.scorer}`} className="border-t">
                  <td className="p-2">
                    {item.case_id} · revision {item.case_revision}
                  </td>
                  <td className="p-2">
                    {item.scorer.replace(/_/g, " ")} · v{item.scorer_version}
                  </td>
                  <td className="p-2">
                    {item.before} → {item.after}
                    {item.after_trace_id && onInspectTrace && (
                      <Button
                        variant="link"
                        className="min-h-11"
                        onClick={() => onInspectTrace(item.after_trace_id!)}
                      >
                        Inspect regressed trace
                      </Button>
                    )}
                  </td>
                  <td className="p-2">
                    {item.measurements
                      ?.map(
                        (measurement) =>
                          `${measurement.name.replace(/_/g, " ")}: ${measurement.before} → ${measurement.after}`,
                      )
                      .join("; ") || "No comparable measurement"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!!report.changed_dependencies?.length && (
        <p className="break-words">
          Changed dependencies:{" "}
          {report.changed_dependencies
            .map(
              (item) =>
                `${item.category.replace(/_/g, " ")}${item.fields?.length ? ` (${item.fields.join(", ")})` : ""}`,
            )
            .join("; ")}
          . Their contribution to the regression remains a hypothesis.
        </p>
      )}
      {report.findings?.map((finding, index) => (
        <div key={index} className="space-y-1 break-words">
          <p>
            {finding.hypothesis ? "Hypothesis: " : "Observed: "}
            {finding.detail}
          </p>
          {finding.semantic && (
            <p>
              Semantic View {finding.semantic.view_id} · version{" "}
              {finding.semantic.version}
            </p>
          )}
          {finding.collisions?.map((collision) => (
            <p key={collision.alias}>
              Alias {collision.alias}: {collision.metrics.join(", ")}
            </p>
          ))}
          {finding.changes?.map((change) => (
            <p key={change.metric}>
              {change.metric}: changed {change.fields.join(", ")}.
            </p>
          ))}
        </div>
      ))}
    </div>
  );
}
