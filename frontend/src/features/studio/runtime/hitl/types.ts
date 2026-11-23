/**
 * Human-in-the-loop requests: the turn pauses and asks the person who asked it.
 *
 * The server contract lives in `backend/app/modules/assistant/human_input.py`.
 * Requests arrive as `input.required` events on a `hitl_request` part and settle
 * with `input.resolved`; answers go to
 * `POST /agents/{agent}/threads/{thread}/runs/{run}/inputs/{request}`.
 */

export type HitlKind = "approval" | "question" | "form" | "choice";

/** How a request settled; `pending` while it waits. */
export type HitlStatus =
  | "pending"
  | "answered"
  | "declined"
  | "allowed"
  | "denied"
  | "edited"
  | "refused"
  | "expired"
  | "cancelled";

export type ApprovalRequest = {
  kind: "approval";
  title: string;
  classification: "read_only" | "session_change" | "destructive";
  /** Why it waits: the agent asks first, it changes data, or it switches role. */
  reason: "asks_first" | "changes_data" | "changes_role";
  /** The role the step runs as. */
  role: string;
  previewKind: "sql" | "semantic" | "text";
  /** Redacted SQL, the semantic question, or a description. */
  preview: string;
  allowSession: boolean;
  objects?: string[];
  semantic?: { question: string; view: string | null };
  editable?: { argument: string; label: string; language?: "sql" | "text"; maxLength?: number };
};

export type QuestionRequest = {
  kind: "question";
  title: string;
  detail?: string;
  choices: string[];
  allowFreeText: boolean;
};

export type FormField = {
  name: string;
  title: string;
  description?: string;
  widget: "text" | "select" | "date" | "date_range" | "number" | "integer" | "toggle";
  values?: string[];
  minimum?: number;
  maximum?: number;
  maxLength?: number;
  default?: string | number | boolean;
};

export type FormRequest = {
  kind: "form";
  title: string;
  detail?: string;
  fields: FormField[];
  required: string[];
  submitLabel?: string;
};

export type ChoiceOption = {
  id: string;
  title: string;
  description?: string;
  badges?: string[];
  recommended?: boolean;
};

export type ChoiceRequest = {
  kind: "choice";
  title: string;
  detail?: string;
  options: ChoiceOption[];
};

export type HitlRequestSpec = ApprovalRequest | QuestionRequest | FormRequest | ChoiceRequest;

/** The body of an answer, as the endpoint takes it. */
export type HitlAnswerBody =
  | { action: "allow_once" | "allow_session"; edited_arguments?: Record<string, string> }
  | { action: "deny" }
  | { action: "decline" }
  | { action: "answer"; answer: Record<string, unknown> };

export type FormValues = Record<string, unknown>;
