import type { CitationBlock } from "@/features/assistant/types";
import type { RailStep } from "./thought-turn";

/** What a tool reaches, which sets its icon and accent under the answer. */
export type ToolKind = "mcp" | "skill" | "data" | "chart" | "web" | "other";

export type ToolCallStatusLabel = "success" | "error" | "cancelled";

/** One call a chip stands for, as its detail card lists it. */
export type ToolChipCall = {
  /** The rail step of this call. */
  stepId: string;
  status: ToolCallStatusLabel;
  /** Arguments or statement, redacted and bounded. */
  input?: string;
  /** Whether the input is a SQL statement. */
  sql: boolean;
  /** The result summary, redacted and bounded. */
  result?: string;
  durationMs?: number;
};

/** One chip: a distinct tool and every call the turn made to it. */
export type ToolChip = {
  key: string;
  kind: ToolKind;
  /** The tool in plain words: "fx convert", "Sales playbook". */
  name: string;
  /** Where it ran: the MCP server, "Skill", "SQL". */
  source: string;
  status: ToolCallStatusLabel;
  calls: ToolChipCall[];
  /** Web pages the tool returned. */
  links: { title: string; url: string }[];
};

/**
 * Tools that ask the user rather than reach anything, and the unnamed part an
 * older stored turn kept for a repeat answered from an earlier result.
 */
const HIDDEN = new Set(["request_human_input", "suggest_follow_ups", "tool"]);

const DATA_SOURCES: Record<string, string> = {
  semantic_query: "Semantic model",
  semantic_search: "Semantic model",
  semantic_view_query: "Semantic view",
  query_execute: "Database",
  catalog_search: "Catalog",
  compute_metrics: "Results",
};

/** Short names for Nova's own tools; the rail keeps the full sentences. */
const CHIP_NAMES: Record<string, string> = {
  semantic_query: "Model query",
  semantic_search: "Model search",
  semantic_view_query: "View query",
  query_execute: "SQL query",
  catalog_search: "Catalog search",
  compute_metrics: "Calculation",
  data_to_chart: "Chart",
  dataviz_query: "Dashboard query",
  web_search: "Web search",
};

/** "native_dashboard_read" → "Dashboard read". */
function humanise(name: string): string {
  const words = name.replace(/^native_/, "").replace(/_/g, " ").trim();
  return words ? words.replace(/^\w/, (c) => c.toUpperCase()) : "Tool";
}

const INPUT_LIMIT = 600;
const RESULT_LIMIT = 400;

type ToolLike = {
  stepId: string;
  toolName: string;
  skillName?: string;
  displayName?: string;
  status: string;
  preview?: string;
  detail?: string;
  durationMs?: number;
};

function statusLabel(status: string): ToolCallStatusLabel {
  if (status === "failed" || status === "denied") return "error";
  if (status === "cancelled") return "cancelled";
  if (status === "done" || status === "approved") return "success";
  // The strip shows once the run ended: a call still open never confirmed.
  return "cancelled";
}

/** The kind, plain name and source of a tool. */
export function classifyTool(
  toolName: string,
  skillName?: string,
  displayName?: string,
): { kind: ToolKind; name: string; source: string } {
  if (toolName === "load_skill") {
    // A skill is loaded by its slug ("monthly-revenue-review"); people read words.
    const name = skillName && /^[a-z0-9]+(?:[-_][a-z0-9]+)+$/.test(skillName) ? humanise(skillName.replace(/-/g, "_")) : skillName;
    return { kind: "skill", name: name || "Skill", source: "Skill" };
  }
  if (toolName.startsWith("mcp_") || (displayName && /^[^:]+:\s*\S/.test(displayName) && !DATA_SOURCES[toolName])) {
    const label = displayName || "Connected tool";
    const split = label.indexOf(":");
    return split > 0
      ? { kind: "mcp", name: label.slice(split + 1).trim(), source: label.slice(0, split).trim() }
      : { kind: "mcp", name: label, source: "Connector" };
  }
  const plain = CHIP_NAMES[toolName] ?? humanise(toolName);
  if (DATA_SOURCES[toolName]) return { kind: "data", name: plain, source: DATA_SOURCES[toolName] };
  if (toolName === "data_to_chart" || toolName.startsWith("native_") || toolName === "dataviz_query") {
    return { kind: "chart", name: plain, source: "Dataviz" };
  }
  if (toolName === "web_search") return { kind: "web", name: plain, source: "Web" };
  return { kind: "other", name: plain, source: "Nova" };
}

const SECRET_FIELD =
  /("?(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization|credential|private[_-]?key)"?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;}&]+)/gi;
const BEARER = /\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}/gi;
const URL_SECRET = /([?&](?:token|key|api_key|access_token|signature|sig|secret|password)=)[^&\s"']+/gi;
// A long run of opaque characters (keys, signed blobs) carries no meaning for a reader.
const OPAQUE = /\b(?=[A-Za-z0-9_+/=-]*\d)(?=[A-Za-z0-9_+/=-]*[A-Za-z])[A-Za-z0-9_+/=-]{40,}\b/g;

/**
 * Text safe to show: credential values, bearer tokens and opaque runs are
 * replaced. The server redacts before sending; this guards what it missed.
 */
export function redact(text: string, limit = INPUT_LIMIT): string {
  const clean = text
    .replace(BEARER, "$1 [redacted]")
    .replace(SECRET_FIELD, (match, field: string, value: string) =>
      /^(Bearer|Basic)$/i.test(value) ? match : `${field}[redacted]`)
    .replace(URL_SECRET, "$1[redacted]")
    .replace(OPAQUE, "[redacted]")
    .trim();
  return clean.length > limit ? `${clean.slice(0, limit - 1).trimEnd()}…` : clean;
}

const EXTERNAL_SOURCE = /\bexternal:([^/\n]+?)\/([A-Za-z0-9_-]+)/g;

const EXTERNAL_CONTENT = /<EXTERNAL_CONTENT\b[^>]*>([\s\S]*?)(?:<\/EXTERNAL_CONTENT>|$)/;

/**
 * A result as people read it: a connector's reply without the guidance written
 * for the model around it, and an internal source token
 * (``external:Server/tool``) in words.
 */
function plain(text: string): string {
  const wrapped = EXTERNAL_CONTENT.exec(text);
  if (wrapped) text = wrapped[1].trim() || text;
  return text.replace(EXTERNAL_SOURCE, (_match, server: string, tool: string) => `${server.trim()} (${tool.replace(/_/g, " ")})`);
}

function looksLikeSql(text: string): boolean {
  return /^\s*(SELECT|WITH|SHOW|DESCRIBE|EXPLAIN)\b/i.test(text) && /\bFROM\b|\bSHOW\b|\bDESCRIBE\b/i.test(text);
}

function toolLikes(steps: RailStep[]): ToolLike[] {
  return steps
    .filter((step) => step.kind === "tool")
    .map((step) => ({
      stepId: step.id,
      toolName: step.label,
      skillName: step.skillName,
      displayName: step.displayName,
      status: step.status,
      preview: step.label === "load_skill" ? undefined : step.preview,
      detail: step.detail,
      durationMs: step.durationMs,
    }));
}

const SEVERITY: Record<ToolCallStatusLabel, number> = { success: 0, cancelled: 1, error: 2 };

/**
 * The tools a turn used, one chip per distinct tool with its calls, plus the
 * citations no chip absorbed. A connector's citation repeats its chip and a
 * web page becomes a link in the web chip.
 */
export function toolChips(
  steps: RailStep[],
  citations: CitationBlock[] = [],
): { chips: ToolChip[]; sources: CitationBlock[] } {
  const chips = new Map<string, ToolChip>();
  for (const tool of toolLikes(steps)) {
    if (HIDDEN.has(tool.toolName)) continue;
    const { kind, name, source } = classifyTool(tool.toolName, tool.skillName, tool.displayName);
    const key = `${kind}:${source}:${name}`;
    const chip = chips.get(key) ?? { key, kind, name, source, status: "success", calls: [], links: [] };
    const status = statusLabel(tool.status);
    const input = tool.preview?.trim() ? redact(tool.preview) : undefined;
    const result = tool.detail?.trim() ? redact(plain(tool.detail), RESULT_LIMIT) : undefined;
    chip.calls.push({
      stepId: tool.stepId,
      status,
      input,
      sql: Boolean(input && looksLikeSql(input)),
      result: result && result !== input ? result : undefined,
      durationMs: tool.durationMs,
    });
    if (SEVERITY[status] > SEVERITY[chip.status]) chip.status = status;
    chips.set(key, chip);
  }
  const web = [...chips.values()].find((chip) => chip.kind === "web");
  const sources: CitationBlock[] = [];
  const seen = new Set<string>();
  for (const citation of citations) {
    if (citation.source === "external_mcp") continue;
    const id = citation.url ?? citation.title ?? citation.source ?? "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    if (web && citation.source === "web" && citation.url) {
      web.links.push({ title: citation.title || citation.url, url: citation.url });
      continue;
    }
    sources.push(citation);
  }
  return { chips: [...chips.values()], sources };
}

/** A duration the way the rail reads it: "640 ms", "2.4 s", "1 m 5 s". */
export function formatDuration(ms: number | undefined): string | undefined {
  if (ms === undefined || !Number.isFinite(ms) || ms < 0) return undefined;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  return `${Math.floor(seconds / 60)} m ${Math.round(seconds % 60)} s`;
}

/** Suggested questions, once each, without any that only name a tool. */
export function followUpQuestions(suggestions: string[] | undefined, chips: ToolChip[]): string[] {
  const names = new Set(chips.flatMap((chip) => [chip.name, `${chip.source}: ${chip.name}`]).map(normal));
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of suggestions ?? []) {
    const question = raw.trim();
    const key = normal(question);
    if (!question || seen.has(key) || names.has(key) || /\bmcp_[0-9a-f]{6,}/i.test(question)) continue;
    seen.add(key);
    out.push(question);
  }
  return out;
}

function normal(text: string): string {
  return text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}
