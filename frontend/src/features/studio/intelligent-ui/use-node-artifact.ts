import { useContext } from "react";
import { UiRuntimeContext } from "./runtime-context";
import type { UiArtifactDraft } from "./types";

/** Save state for a chart, table or map whose records come from one verified result. */
export function useNodeArtifact(
  data: unknown,
  draft: UiArtifactDraft,
): { onSave: () => void; saving: boolean; saved: boolean } | null {
  const { artifacts } = useContext(UiRuntimeContext);
  const evidenceId = artifacts?.evidenceFor(data);
  if (!artifacts || !evidenceId || (artifacts.kinds && !artifacts.kinds.includes(draft.kind)))
    return null;
  const target = { ...draft, evidenceId };
  const status = artifacts.status(target);
  return {
    onSave: () => artifacts.save(target),
    saving: status === "saving",
    saved: status === "saved",
  };
}
