/** The chart drafts of one turn, as its components and panel use them. */
import { createContext, useContext } from "react";
import { useNavigate } from "@tanstack/react-router";
import { NATIVE_DASHBOARD_OPEN } from "@/features/native-dataviz/dashboard/events";
import type { ArtifactPart } from "../turn-reducer";
import type { ArtifactEventRecord } from "./api";
import type { ArtifactSaved } from "./types";

export type ArtifactScope = {
  /** The turn's drafts by `draft_ref`. */
  byRef: Map<string, ArtifactPart>;
  /** The turn is stored: drafts can be read, edited and saved. */
  ready: boolean;
  open: (partId: string) => void;
  /** Apply a server event an edit or a save returned. */
  apply: (record: ArtifactEventRecord) => void;
};

export const ArtifactContext = createContext<ArtifactScope | null>(null);

/** The draft a chart component belongs to, when its turn announced one. */
export function useArtifact(draftRef: string | undefined): { part: ArtifactPart; scope: ArtifactScope } | null {
  const scope = useContext(ArtifactContext);
  const part = draftRef ? scope?.byRef.get(draftRef) : undefined;
  return scope && part ? { part, scope } : null;
}

export function artifactScope(
  parts: { type: string }[],
  fields: Omit<ArtifactScope, "byRef">,
): ArtifactScope {
  const byRef = new Map<string, ArtifactPart>();
  for (const part of parts) {
    if (part.type === "artifact_preview") {
      const artifact = part as ArtifactPart;
      byRef.set(artifact.draft.draft_ref, artifact);
    }
  }
  return { byRef, ...fields };
}

/** Open a saved chart's dashboard in the Dataviz tab. */
export function useOpenDashboard() {
  const navigate = useNavigate();
  return (saved: Pick<ArtifactSaved, "dashboard_id" | "workspace_id">) => {
    const destination = { dashboardId: saved.dashboard_id, workspaceId: saved.workspace_id };
    window.dispatchEvent(new CustomEvent(NATIVE_DASHBOARD_OPEN, { detail: destination }));
    try {
      localStorage.setItem("nova.dataviz.native.open", JSON.stringify(destination));
    } catch {
      // The Dataviz tab already received the event; a reload starts on its list.
    }
    void navigate({ to: "/studio", search: (prev) => ({ ...prev, tab: "dataviz" }) });
  };
}

