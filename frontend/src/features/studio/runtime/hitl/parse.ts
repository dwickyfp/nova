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

/** Badges that only repeat the card's own "Suggested" mark. */
const RECOMMENDED_BADGES = new Set(["recommended", "suggested", "best", "best option"]);

function option(value: unknown): ChoiceOption | null {
  const data = record(value);
  if (!data || typeof data.id !== "string" || typeof data.title !== "string") return null;
  const all = strings(data.badges);
  const badges = all.filter((badge) => !RECOMMENDED_BADGES.has(badge.trim().toLowerCase()));
  return {
    id: data.id,
    title: data.title,
    ...(text(data.description) ? { description: text(data.description) } : {}),
    ...(badges.length ? { badges } : {}),
    ...(data.recommended === true || badges.length < all.length ? { recommended: true } : {}),
  };
}

function approval(data: Record<string, unknown>): ApprovalRequest | null {
  if (typeof data.classification !== "string" || !CLASSES.has(data.classification)) return null;
  const classification = data.classification as ApprovalRequest["classification"];
  // Earlier versions asked before data steps and sent a preview instead of an
  // action; such a stored request replays as a past step only.
  const legacy = typeof data.action !== "string" || classification === "read_only";
  return {
    kind: "approval",
    title: text(data.title) ?? "Allow this action?",
    classification,
    reason: data.reason === "changes_role" || (data.reason !== "changes_data" && classification === "session_change")
      ? "changes_role"
      : "changes_data",
    role: text(data.role) ?? "",
    action: text(data.action) ?? text(data.preview) ?? "",
    ...(legacy ? { legacy: true } : {}),
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
