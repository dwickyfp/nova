import { createContext } from "react";
import type { DatavizLocator } from "../dataviz/api";
import type { UiSource } from "./types";

/** A data view inside an answer, named the way its program names it. */
export type UiDatavizTarget = NonNullable<DatavizLocator["node"]>;

export type UiArtifacts = {
  /** The verified result a node's records come from, when it can be told. */
  evidenceFor: (data: unknown) => string | null;
  /** Where this view lives in Nova's record, for the server to verify. */
  locate: (target: UiDatavizTarget) => DatavizLocator | null;
};

export const UiRuntimeContext = createContext<{
  readOnly: boolean;
  source?: UiSource;
  markAction: (id: string) => void;
  artifacts?: UiArtifacts;
}>({ readOnly: true, markAction: () => {} });
