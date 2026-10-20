import type { StudioArtifactRefresh } from "@/features/agents/api";

export function chartSpecWithRows(
  data: Pick<StudioArtifactRefresh, "columns" | "rows"> & {
    artifact: { chart_spec?: Record<string, unknown> | null };
  },
): string {
  const spec = structuredClone(data.artifact.chart_spec ?? {});
  const values = data.rows.map((row) =>
    Object.fromEntries(
      data.columns.map((column, index) => [column, row[index] ?? null]),
    ),
  );
  delete spec.datasets;
  delete spec.title;
  spec.data = { values };
  return JSON.stringify(spec);
}
