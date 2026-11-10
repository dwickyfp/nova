/** A Workspace file opens as a notebook when it is a Jupyter document. */
export function isNotebookName(name: string | null | undefined): boolean {
  return !!name && name.toLowerCase().endsWith(".ipynb");
}

/** The next free "Untitled notebook" name among existing names. */
export function nextNotebookName(existing: Iterable<string>): string {
  const taken = new Set([...existing].map((name) => name.toLowerCase()));
  const base = "Untitled notebook";
  if (!taken.has(`${base}.ipynb`.toLowerCase())) return `${base}.ipynb`;
  let i = 2;
  while (taken.has(`${base} ${i}.ipynb`.toLowerCase())) i++;
  return `${base} ${i}.ipynb`;
}

const TEXT_FILES = [".sql", ".ipynb", ".py", ".md", ".txt", ".json"];

/**
 * Files Workspaces opens in a tab. Uploaded data (CSV, Parquet, Excel,
 * images) stays a file that notebooks read; it is not opened as SQL text.
 * A name without an extension is an older worksheet.
 */
export function isEditableName(name: string): boolean {
  const lower = name.toLowerCase();
  if (!/\.[a-z0-9]+$/.test(lower)) return true;
  return TEXT_FILES.some((extension) => lower.endsWith(extension));
}
