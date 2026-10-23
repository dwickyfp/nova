import { useMemo } from "react";
import { chartSpecToUiBlock } from "./chart-spec-block";
import { IntelligentUi } from "./renderer";

/** A stored Vega-Lite chart drawn with the intelligent UI components. */
export function UiChart({
  spec,
  onSave,
  saving = false,
  saved = false,
}: {
  spec: string;
  onSave?: () => void;
  saving?: boolean;
  saved?: boolean;
}) {
  const block = useMemo(() => chartSpecToUiBlock(spec), [spec]);
  if (!block)
    return <p className="text-sm text-muted-foreground">This chart could not be displayed.</p>;
  return (
    <IntelligentUi
      block={block}
      complete
      artifactKinds={["chart"]}
      onSaveArtifact={onSave ? () => onSave() : undefined}
      artifactStatus={() => (saving ? "saving" : saved ? "saved" : "idle")}
    />
  );
}
