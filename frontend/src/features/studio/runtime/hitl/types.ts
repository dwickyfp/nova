/**
 * Human-in-the-loop requests: the turn pauses and asks the person who asked it.
 *
 * The server contract lives in `backend/app/modules/assistant/human_input.py`.
 * Requests arrive as `input.required` events on a `hitl_request` part and settle
 * with `input.resolved`; answers go to
 * `POST /agents/{agent}/threads/{thread}/runs/{run}/inputs/{request}`.
 */

export type HitlKind = "approval" | "question" | "form" | "choice";

/**
 * How a request settled; `pending` while it waits. `edited` and `refused` come
 * only from conversations stored by an earlier version and replay as they were.
 */
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

/**
 * Approve one action that changes something (an automation, an outside tool,
 * a write, a role switch). Data queries never ask.
 */
export type ApprovalRequest = {
  kind: "approval";
  title: string;
  classification: "read_only" | "session_change" | "destructive";
  /** Why it waits: it changes something, or it switches the role. */
  reason: "changes_data" | "changes_role";
  /** The role the action runs as. */
  role: string;
  /** What the action does, in plain words. */
  action: string;
  /**
   * Stored by an earlier version that asked before data steps: it replays as
   * a past step and never offers its buttons.
   */
  legacy?: boolean;
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
  | { action: "allow_once" }
  | { action: "deny" }
  | { action: "decline" }
  | { action: "answer"; answer: Record<string, unknown> };

export type FormValues = Record<string, unknown>;
