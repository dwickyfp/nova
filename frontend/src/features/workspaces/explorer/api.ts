import { api } from "@/lib/api-client";
import type { WorkspaceEntry, WorkspaceTreeResponse } from "../types";

export const PERSONAL = "personal";

/** A workspace as the person sees it: their own, or one shared with a role of theirs. */
export type WorkspaceSpace = {
  id: string;
  name: string;
  kind: "personal" | "shared";
  owner: string;
  permission: "owner" | "edit" | "view";
};

export type WorkspaceShare = {
  role: string;
  permission: "view" | "edit";
  granted_by?: string | null;
};

export const canEditSpace = (space: WorkspaceSpace | undefined) =>
  !space || space.permission !== "view";

export const workspaceApi = {
  spaces: () => api.get<{ spaces: WorkspaceSpace[] }>("/workspaces/spaces"),
  createSpace: (name: string) => api.post<{ space: WorkspaceSpace }>("/workspaces/spaces", { name }),
  renameSpace: (id: string, name: string) =>
    api.patch<{ space: WorkspaceSpace }>(`/workspaces/spaces/${id}`, { name }),
  deleteSpace: (id: string) => api.delete<{ success: boolean }>(`/workspaces/spaces/${id}`),
  sharedTree: (id: string) => api.get<WorkspaceTreeResponse>(`/workspaces/spaces/${id}/tree`),
  shares: (id: string) => api.get<{ shares: WorkspaceShare[] }>(`/workspaces/spaces/${id}/shares`),
  setShares: (id: string, shares: WorkspaceShare[]) =>
    api.put<{ shares: WorkspaceShare[] }>(`/workspaces/spaces/${id}/shares`, {
      shares: shares.map(({ role, permission }) => ({ role, permission })),
    }),
  roles: () => api.get<{ roles: string[] }>("/agents/roles"),
  createFolder: (space: string, parentPath: string, name: string) =>
    api.post<{ entry: WorkspaceEntry }>("/workspaces/folders", {
      space,
      parent_path: parentPath,
      name,
    }),
  /** Rename an entry, or move it to another folder of the same workspace. */
  move: (id: string, name: string, parentPath: string) =>
    api.post<{ entry: WorkspaceEntry }>("/workspaces/rename", { id, name, parent_path: parentPath }),
  remove: (id: string) => api.delete<{ success: boolean }>(`/workspaces/files/${id}`),
};
