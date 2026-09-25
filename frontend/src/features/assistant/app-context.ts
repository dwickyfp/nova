/** The bounded application state Nove receives with each turn. */
export type NoveAppContext = {
  version: 1;
  surface: { id: string; route: string; title?: string | null };
  entity?: {
    type: string;
    id?: string | null;
    name?: string | null;
    metadata?: Record<string, unknown>;
  };
  selection?: {
    type?: string | null;
    ids?: string[];
    text?: string | null;
    metadata?: Record<string, unknown>;
  };
  editor?: {
    documentId?: string | null;
    language?: string | null;
    selectedText?: string | null;
    cursor?: { line?: number; column?: number };
    dirty?: boolean;
  };
  execution?: {
    type?: string | null;
    executionId?: string | null;
    status?: "idle" | "running" | "success" | "error";
    errorCode?: string | null;
    errorMessage?: string | null;
    elapsedMs?: number | null;
    rowCount?: number | null;
    resultSchema?: Array<{ name: string; type?: string | null }>;
  };
  view?: {
    activeTab?: string | null;
    filters?: Record<string, unknown>;
    search?: string | null;
    sort?: string | null;
  };
  domain?: {
    database?: string | null;
    schema?: string | null;
    role?: string | null;
    semanticModel?: string | null;
    providerId?: string | null;
  };
  capabilities: string[];
  events?: NoveApplicationEvent[];
};

export type NoveSurfaceContext = Partial<
  Omit<NoveAppContext, "version" | "surface" | "capabilities" | "events">
>;

export type NoveApplicationEvent = {
  id: string;
  timestamp: string;
  source: "assistant" | "surface" | "execution" | "user";
  type: string;
  surfaceId?: string;
  correlationId?: string;
  artifactId?: string;
  executionId?: string;
  status?: "success" | "failure";
  payload?: Record<string, unknown>;
};

export type NoveEventInput = Omit<NoveApplicationEvent, "id" | "timestamp">;

const MAX_CONTEXT_BYTES = 16_384;
const MAX_EVENTS = 8;
const SENSITIVE_TEXT =
  /password|passwd|passphrase|secret|token|credential|api[\s._-]*key|access[\s._-]*key|private[\s._-]*key|account[\s._-]*key|authorization|authentication|bearer|identified\s+by|cookie/i;
const CREDENTIAL_VALUE =
  /\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}\b|-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----|\b(?:sk|gsk|xai|ghp|gho|ghu|ghs|ghr)[-_][A-Za-z0-9_-]{16,}\b|\bxox[baprs][-_][A-Za-z0-9-]{10,}\b|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b|:\/\/[^\s/@]+:[^\s/@]+@/i;

function containsSensitiveText(value: string): boolean {
  return SENSITIVE_TEXT.test(value) || CREDENTIAL_VALUE.test(value);
}

function clip(value: unknown, limit = 240): string | undefined {
  if (typeof value !== "string") return undefined;
  // Inspect before clipping: otherwise a credential after the limit survives in a shorter field.
  return containsSensitiveText(value) ? "[REDACTED]" : value.slice(0, limit);
}

function clipSensitiveText(value: unknown, limit: number): string | undefined {
  return clip(value, limit);
}

function scalarRecord(
  value: unknown,
  maxEntries = 12,
  allowSql = false,
): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return undefined;
  const result: Record<string, unknown> = {};
  for (const [key, entry] of Object.entries(value).slice(0, maxEntries)) {
    if (containsSensitiveText(key)) continue;
    const safeKey = clip(key, 64);
    if (!safeKey) continue;
    if (typeof entry === "string") {
      if (allowSql && key === "sql") {
        if (!containsSensitiveText(entry)) result.sql = clip(entry, 4_000);
      } else result[safeKey] = clip(entry);
    } else if (typeof entry === "number" && Number.isFinite(entry))
      result[safeKey] = entry;
    else if (typeof entry === "boolean" || entry === null)
      result[safeKey] = entry;
  }
  return Object.keys(result).length ? result : undefined;
}

/** Only named fields cross the model boundary; unknown surface state is discarded. */
export function boundAppContext(context: NoveAppContext): NoveAppContext {
  const current: NoveAppContext = {
    version: 1,
    surface: {
      id: clip(context.surface.id, 96) || "nova",
      route: clip(context.surface.route, 256) || "/",
      title: clip(context.surface.title, 120),
    },
    capabilities: [...new Set(context.capabilities)]
      .filter((value) => !containsSensitiveText(value))
      .slice(0, 32)
      .map((value) => clip(value, 96) || "")
      .filter(Boolean),
  };
  if (context.entity)
    current.entity = {
      type: clip(context.entity.type, 64) || "unknown",
      id: clip(context.entity.id, 160),
      name: clip(context.entity.name, 160),
      metadata: scalarRecord(context.entity.metadata),
    };
  if (context.selection)
    current.selection = {
      type: clip(context.selection.type, 64),
      ids: context.selection.ids?.slice(0, 20).map((id) => clip(id, 160) || ""),
      text: clipSensitiveText(context.selection.text, 2_000),
      metadata: scalarRecord(context.selection.metadata),
    };
  if (context.editor)
    current.editor = {
      documentId: clip(context.editor.documentId, 160),
      language: clip(context.editor.language, 32),
      selectedText: clipSensitiveText(context.editor.selectedText, 2_000),
      cursor: context.editor.cursor
        ? {
            line: Number.isSafeInteger(context.editor.cursor.line)
              ? context.editor.cursor.line
              : undefined,
            column: Number.isSafeInteger(context.editor.cursor.column)
              ? context.editor.cursor.column
              : undefined,
          }
        : undefined,
      dirty: context.editor.dirty === true,
    };
  if (context.execution)
    current.execution = {
      type: clip(context.execution.type, 64),
      executionId: clip(context.execution.executionId, 160),
      status: context.execution.status,
      errorCode: clip(context.execution.errorCode, 80),
      errorMessage: clipSensitiveText(context.execution.errorMessage, 1_500),
      elapsedMs:
        typeof context.execution.elapsedMs === "number" &&
        Number.isFinite(context.execution.elapsedMs)
          ? context.execution.elapsedMs
          : undefined,
      rowCount:
        typeof context.execution.rowCount === "number" &&
        Number.isFinite(context.execution.rowCount)
          ? context.execution.rowCount
          : undefined,
      resultSchema: context.execution.resultSchema
        ?.slice(0, 30)
        .map((item) => ({
          name: clip(item.name, 96) || "",
          type: clip(item.type, 64),
        })),
    };
  if (context.view)
    current.view = {
      activeTab: clip(context.view.activeTab, 80),
      filters: scalarRecord(context.view.filters),
      search: clip(context.view.search, 240),
      sort: clip(context.view.sort, 80),
    };
  if (context.domain)
    current.domain = {
      database: clip(context.domain.database, 120),
      schema: clip(context.domain.schema, 120),
      role: clip(context.domain.role, 120),
      semanticModel: clip(context.domain.semanticModel, 160),
      providerId: clip(context.domain.providerId, 160),
    };
  current.events = context.events?.slice(-MAX_EVENTS).map((event) => ({
    id: clip(event.id, 96) || "",
    timestamp: clip(event.timestamp, 40) || "",
    source: event.source,
    type: clip(event.type, 80) || "unknown",
    surfaceId: clip(event.surfaceId, 96),
    correlationId: clip(event.correlationId, 160),
    artifactId: clip(event.artifactId, 160),
    executionId: clip(event.executionId, 160),
    status: event.status,
    payload: scalarRecord(event.payload, 16, true),
  }));
  // Trim the least important data first. Surface and capability identity remain.
  while (
    new TextEncoder().encode(JSON.stringify(current)).length > MAX_CONTEXT_BYTES
  ) {
    if (current.events?.length) current.events.shift();
    else if (current.selection?.text)
      current.selection.text = current.selection.text.slice(
        0,
        Math.floor(current.selection.text.length / 2),
      );
    else if (current.editor?.selectedText)
      current.editor.selectedText = current.editor.selectedText.slice(
        0,
        Math.floor(current.editor.selectedText.length / 2),
      );
    else if (current.execution?.errorMessage)
      current.execution.errorMessage = current.execution.errorMessage.slice(
        0,
        Math.floor(current.execution.errorMessage.length / 2),
      );
    else if (current.entity?.metadata) delete current.entity.metadata;
    else if (current.selection?.metadata) delete current.selection.metadata;
    else if (current.view?.filters) delete current.view.filters;
    else if (current.execution?.resultSchema)
      delete current.execution.resultSchema;
    else if (current.selection) delete current.selection;
    else if (current.editor) delete current.editor;
    else if (current.execution) delete current.execution;
    else break;
  }
  return current;
}

let eventSequence = 0;
export function createApplicationEvent(
  input: NoveEventInput,
): NoveApplicationEvent {
  eventSequence += 1;
  return {
    ...input,
    id: `${Date.now()}-${eventSequence}`,
    timestamp: new Date().toISOString(),
  };
}
