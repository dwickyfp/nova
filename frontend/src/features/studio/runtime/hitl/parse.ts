/**
 * Read a human-input request from the wire. Anything malformed is dropped
 * (`null`): the card then shows nothing it cannot render rather than guessing.
 */
import type {
  ApprovalRequest,
  ChoiceOption,
  FormField,
  HitlRequestSpec,
  HitlStatus,
} from "./types";

const WIDGETS = new Set(["text", "select", "date", "date_range", "number", "integer", "toggle"]);
const STATUSES = new Set<HitlStatus>([
  "pending", "answered", "declined", "allowed", "denied", "edited", "refused", "expired", "cancelled",
]);
const CLASSES = new Set(["read_only", "session_change", "destructive"]);
const REASONS = new Set(["asks_first", "changes_data", "changes_role"]);

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

const text = (value: unknown): string | undefined => (typeof value === "string" ? value : undefined);
const num = (value: unknown): number | undefined => (typeof value === "number" && Number.isFinite(value) ? value : undefined);
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

export function hitlStatus(value: unknown): HitlStatus | null {
  return typeof value === "string" && STATUSES.has(value as HitlStatus) ? (value as HitlStatus) : null;
}

function field(value: unknown): FormField | null {
  const data = record(value);
  if (!data || typeof data.name !== "string" || typeof data.title !== "string") return null;
  if (typeof data.widget !== "string" || !WIDGETS.has(data.widget)) return null;
  const widget = data.widget as FormField["widget"];
  if (widget === "select" && strings(data.values).length === 0) return null;
  const fallback = data.default;
  return {
    name: data.name,
    title: data.title,
    widget,
    ...(text(data.description) ? { description: text(data.description) } : {}),
    ...(widget === "select" ? { values: strings(data.values) } : {}),
    ...(num(data.minimum) !== undefined ? { minimum: num(data.minimum) } : {}),
    ...(num(data.maximum) !== undefined ? { maximum: num(data.maximum) } : {}),
    ...(num(data.max_length) !== undefined ? { maxLength: num(data.max_length) } : {}),
    ...(typeof fallback === "string" || typeof fallback === "number" || typeof fallback === "boolean"
      ? { default: fallback }
      : {}),
  };
}

function option(value: unknown): ChoiceOption | null {
  const data = record(value);
  if (!data || typeof data.id !== "string" || typeof data.title !== "string") return null;
  return {
    id: data.id,
    title: data.title,
    ...(text(data.description) ? { description: text(data.description) } : {}),
    ...(strings(data.badges).length ? { badges: strings(data.badges) } : {}),
    ...(data.recommended === true ? { recommended: true } : {}),
  };
}

function approval(data: Record<string, unknown>): ApprovalRequest | null {
  if (typeof data.classification !== "string" || !CLASSES.has(data.classification)) return null;
  const semantic = record(data.semantic);
  const editable = record(data.editable);
  const kind = data.preview_kind;
  return {
    kind: "approval",
    title: text(data.title) ?? "Approve this step?",
    classification: data.classification as ApprovalRequest["classification"],
    reason: typeof data.reason === "string" && REASONS.has(data.reason)
      ? (data.reason as ApprovalRequest["reason"])
      : "asks_first",
    role: text(data.role) ?? "",
    previewKind: kind === "sql" || kind === "semantic" ? kind : "text",
    preview: text(data.preview) ?? "",
    allowSession: data.allow_session === true,
    ...(strings(data.objects).length ? { objects: strings(data.objects) } : {}),
    ...(semantic && typeof semantic.question === "string"
      ? { semantic: { question: semantic.question, view: text(semantic.view) ?? null } }
      : {}),
    ...(editable && typeof editable.argument === "string"
      ? {
          editable: {
            argument: editable.argument,
            label: text(editable.label) ?? editable.argument,
            ...(editable.language === "sql" ? { language: "sql" as const } : { language: "text" as const }),
            ...(num(editable.max_length) !== undefined ? { maxLength: num(editable.max_length) } : {}),
          },
        }
      : {}),
  };
}

/** The request a `hitl_request` part asks, or null when it cannot be shown. */
export function parseHitlRequest(value: unknown): HitlRequestSpec | null {
  const data = record(value);
  if (!data) return null;
  const title = text(data.title);
  const detail = text(data.detail) ? { detail: text(data.detail) } : {};
  switch (data.kind) {
    case "approval":
      return approval(data);
    case "question": {
      const choices = strings(data.choices);
      const allowFreeText = data.allow_free_text !== false;
      if (!title || (!choices.length && !allowFreeText)) return null;
      return { kind: "question", title, ...detail, choices, allowFreeText };
    }
    case "form": {
      const fields = Array.isArray(data.fields) ? data.fields.map(field) : [];
      if (!title || !fields.length || fields.some((item) => item === null)) return null;
      return {
        kind: "form",
        title,
        ...detail,
        fields: fields as FormField[],
        required: strings(data.required),
        ...(text(data.submit_label) ? { submitLabel: text(data.submit_label) } : {}),
      };
    }
    case "choice": {
      const options = Array.isArray(data.options) ? data.options.map(option) : [];
      if (!title || options.length < 2 || options.some((item) => item === null)) return null;
      return { kind: "choice", title, ...detail, options: options as ChoiceOption[] };
    }
    default:
      return null;
  }
}
