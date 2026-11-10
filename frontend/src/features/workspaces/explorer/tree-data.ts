import type { WorkspaceEntry } from "../types";

/** Entries whose path matches, plus the folders that lead to them. */
export function filterEntries(entries: WorkspaceEntry[], search: string): WorkspaceEntry[] {
  const query = search.trim().toLowerCase();
  if (!query) return entries;
  const byPath = new Map(entries.map((entry) => [entry.path, entry]));
  const keep = new Set<string>();
  for (const entry of entries) {
    if (!entry.path.toLowerCase().includes(query)) continue;
    keep.add(entry.path);
    let parent = entry.parent_path;
    while (parent && !keep.has(parent)) {
      keep.add(parent);
      parent = byPath.get(parent)?.parent_path ?? "";
    }
  }
  return entries.filter((entry) => keep.has(entry.path));
}

/** The direct children of a folder ("" is the top level): folders first, then by name. */
export function childrenOf(entries: WorkspaceEntry[], parentPath: string): WorkspaceEntry[] {
  return entries
    .filter((entry) => entry.parent_path === parentPath)
    .sort((a, b) =>
      a.entry_type === b.entry_type
        ? a.name.localeCompare(b.name, undefined, { numeric: true })
        : a.entry_type === "folder"
          ? -1
          : 1,
    );
}

/** An entry and everything under it (what a delete removes). */
export function subtree(entries: WorkspaceEntry[], entry: WorkspaceEntry): WorkspaceEntry[] {
  return entries.filter(
    (item) => item.id === entry.id || item.path.startsWith(`${entry.path}/`),
  );
}

/**
 * Folders an entry may move into: the top level ("") and every folder that is
 * not the entry itself, inside it, or already its parent.
 */
export function moveTargets(entries: WorkspaceEntry[], entry: WorkspaceEntry): string[] {
  const folders = entries
    .filter((item) => item.entry_type === "folder")
    .map((item) => item.path)
    .filter((path) => path !== entry.path && !path.startsWith(`${entry.path}/`))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  return ["", ...folders].filter((path) => path !== entry.parent_path);
}

/** A name not taken in a folder: "Untitled.sql", "Untitled-2.sql", … */
export function freeName(
  entries: WorkspaceEntry[],
  parentPath: string,
  base: string,
  extension: string,
): string {
  const taken = new Set(
    entries.filter((e) => e.parent_path === parentPath).map((e) => e.name.toLowerCase()),
  );
  const first = `${base}${extension}`;
  if (!taken.has(first.toLowerCase())) return first;
  let index = 2;
  while (taken.has(`${base}-${index}${extension}`.toLowerCase())) index += 1;
  return `${base}-${index}${extension}`;
}
