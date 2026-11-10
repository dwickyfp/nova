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


/**
 * Whether notebook HTML needs scripts to show anything (plotly) or nests a
 * frame whose page does (PyGWalker). A nested frame inherits the outer
 * frame's sandbox, so it can only run when the outer one may.
 */
export function needsScripts(markup: string): boolean {
  return /<(?:script|iframe)[\s>]/i.test(markup);
}

/** CSP of an interactive output: inline and blob scripts, and no network at all. */
export const INTERACTIVE_POLICY =
  "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob: data:; " +
  "style-src 'unsafe-inline' blob: data:; img-src data: blob:; font-src data: blob:; " +
  "connect-src data: blob:; worker-src blob:; frame-src blob: data:";

/**
 * Runs first in an interactive output frame. The frame has an opaque origin,
 * so the browser refuses it localStorage, sessionStorage and cookies; pages
 * such as PyGWalker stop when those throw. This gives the frame in-memory
 * stand-ins that last as long as the output is shown and reach nothing else.
 */
export const STORAGE_SHIM =
  "(function(){function m(){var d={};return{getItem:function(k){return Object.prototype." +
  "hasOwnProperty.call(d,k)?d[k]:null},setItem:function(k,v){d[k]=String(v)},removeItem:" +
  "function(k){delete d[k]},clear:function(){d={}},key:function(i){return Object.keys(d)[i]" +
  "||null},get length(){return Object.keys(d).length}}}['localStorage','sessionStorage']." +
  "forEach(function(n){try{window[n].length}catch(e){Object.defineProperty(window,n,{value:" +
  "m(),configurable:true})}});try{document.cookie}catch(e){Object.defineProperty(document," +
  "'cookie',{get:function(){return ''},set:function(){},configurable:true})}})();";
