import type { CellKind, RuntimeStatus } from "./types";

export const NOVA_TABLE_MIME = "application/x-nova-table";

/** Starter code for a data file dropped onto the notebook. */
export function cellForFile(path: string): { kind: CellKind; source: string } | null {
  const quoted = `'${path.replace(/'/g, "''")}'`;
  const lower = path.toLowerCase();
  if (lower.endsWith(".csv") || lower.endsWith(".tsv")) {
    return { kind: "sql", source: `SELECT *\nFROM read_csv(${quoted})\nLIMIT 100` };
  }
  if (lower.endsWith(".parquet")) {
    return { kind: "sql", source: `SELECT *\nFROM read_parquet(${quoted})\nLIMIT 100` };
  }
  if (lower.endsWith(".json") || lower.endsWith(".jsonl") || lower.endsWith(".ndjson")) {
    return { kind: "sql", source: `SELECT *\nFROM read_json(${quoted})\nLIMIT 100` };
  }
  if (lower.endsWith(".xlsx") || lower.endsWith(".xls")) {
    return {
      kind: "python",
      source: `import pandas as pd\n\nsheet = pd.read_excel(${quoted})\nsheet.head(100)`,
    };
  }
  return null;
}

/** Text a reader sees for one table value. */
export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? value.toLocaleString("en-US")
      : value.toLocaleString("en-US", { maximumFractionDigits: 6 });
  }
  return String(value);
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "–";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1000)} s`;
}

type Tone = "neutral" | "busy" | "ready" | "danger";

export const RUNTIME_STATE: Record<RuntimeStatus, { label: string; tone: Tone }> = {
  ABSENT: { label: "Not started", tone: "neutral" },
  PROVISIONING: { label: "Starting", tone: "busy" },
  STARTING: { label: "Starting", tone: "busy" },
  BOOTSTRAPPING: { label: "Preparing", tone: "busy" },
  READY: { label: "Ready", tone: "ready" },
  BUSY: { label: "Running", tone: "busy" },
  STOPPING: { label: "Stopping", tone: "busy" },
  STOPPED: { label: "Stopped", tone: "neutral" },
  FAILED: { label: "Failed to start", tone: "danger" },
  LOST: { label: "Stopped unexpectedly", tone: "danger" },
};

export const DOT: Record<Tone, string> = {
  neutral: "bg-muted-foreground/50",
  busy: "bg-info",
  ready: "bg-success",
  danger: "bg-destructive",
};

export function isLive(status: RuntimeStatus) {
  return !["ABSENT", "STOPPED", "FAILED", "LOST", "STOPPING"].includes(status);
}

