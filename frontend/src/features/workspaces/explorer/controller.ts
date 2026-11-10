import { useCallback, useState } from "react";
import type { WorkspaceEntry } from "../types";
import type { WorkspaceSpace } from "./api";

/** Where a new item goes: a workspace and a folder in it ("" is its top level). */
export type Place = { space: WorkspaceSpace; parentPath: string };

export type ExplorerDialog =
  | { kind: "new-folder"; place: Place }
  | { kind: "new-workspace" }
  | { kind: "rename"; space: WorkspaceSpace; entry: WorkspaceEntry }
  | { kind: "move"; space: WorkspaceSpace; entry: WorkspaceEntry }
  | { kind: "delete"; space: WorkspaceSpace; entry: WorkspaceEntry }
  | { kind: "rename-workspace"; space: WorkspaceSpace }
  | { kind: "delete-workspace"; space: WorkspaceSpace }
  | { kind: "share"; space: WorkspaceSpace };

/**
 * Shared by the sidebar's New button and the file tree, so both open the same
 * dialogs. `place` is where the last selection was: new items go there.
 */
export function useExplorerController(personal: WorkspaceSpace) {
  const [dialog, setDialog] = useState<ExplorerDialog | null>(null);
  const [place, setPlace] = useState<Place>({ space: personal, parentPath: "" });
  const open = useCallback((next: ExplorerDialog) => setDialog(next), []);
  const close = useCallback(() => setDialog(null), []);
  return { dialog, open, close, place, setPlace };
}

export type ExplorerController = ReturnType<typeof useExplorerController>;
