import { SaveArtifactButton } from "../result-table";
import type { UiArtifactDraft } from "./types";
import { useNodeArtifact } from "./use-node-artifact";

export function NodeArtifactButton({
  data,
  noun,
  draft,
}: {
  data: unknown;
  noun: "chart" | "table" | "map";
  draft: UiArtifactDraft;
}) {
  const save = useNodeArtifact(data, draft);
  return save ? <SaveArtifactButton noun={noun} {...save} /> : null;
}
