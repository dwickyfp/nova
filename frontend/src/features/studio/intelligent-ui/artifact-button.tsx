import { useContext } from "react";
import { AddToDatavizButton } from "../dataviz/add-to-dataviz";
import { UiRuntimeContext, type UiDatavizTarget } from "./runtime-context";

/**
 * **Add to Dataviz** for a chart, table or KPI drawn from one verified result.
 * The server decides; an unsupported view simply has no action.
 */
export function NodeDatavizAction({
  data,
  target,
}: {
  data: unknown;
  target: Omit<UiDatavizTarget, "evidence_id">;
}) {
  const { artifacts } = useContext(UiRuntimeContext);
  const evidenceId = artifacts?.evidenceFor(data);
  const locator = artifacts && evidenceId ? artifacts.locate({ ...target, evidence_id: evidenceId }) : null;
  return <AddToDatavizButton locator={locator} />;
}
