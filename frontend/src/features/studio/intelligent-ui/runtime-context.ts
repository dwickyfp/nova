import { createContext } from "react";
import type { UiArtifactDraft, UiSource } from "./types";

export type UiArtifactTarget = UiArtifactDraft & { evidenceId: string };

export type UiArtifacts = {
  /** Which visuals offer saving; all of them when omitted. */
  kinds?: UiArtifactDraft["kind"][];
  /** The verified result a node's records come from, when it can be told. */
  evidenceFor: (data: unknown) => string | null;
  save: (target: UiArtifactTarget) => void;
  status: (target: UiArtifactTarget) => "idle" | "saving" | "saved";
};

export const UiRuntimeContext = createContext<{
  readOnly: boolean;
  source?: UiSource;
  markAction: (id: string) => void;
  artifacts?: UiArtifacts;
}>({ readOnly: true, markAction: () => {} });
