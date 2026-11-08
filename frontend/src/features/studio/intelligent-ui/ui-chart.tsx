import { useMemo } from "react";
import { chartSpecToUiBlock } from "./chart-spec-block";
import { IntelligentUi } from "./renderer";
import type { UiDatavizSource } from "./types";

/** A stored Vega-Lite chart drawn with the intelligent UI components. */
export function UiChart({
  spec,
  dataviz,
}: {
  spec: string;
  /** The stored chart block, so it can offer Add to Dataviz when supported. */
  dataviz?: Omit<UiDatavizSource, "whole">;
}) {
  const block = useMemo(() => chartSpecToUiBlock(spec), [spec]);
  if (!block)
    return <p className="text-sm text-muted-foreground">This chart could not be displayed.</p>;
  return (
    <IntelligentUi block={block} complete dataviz={dataviz ? { ...dataviz, whole: true } : undefined} />
  );
}
