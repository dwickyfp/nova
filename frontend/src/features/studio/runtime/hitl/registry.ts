/**
 * The human-in-the-loop component registry: one entry per request kind.
 *
 * `HitlRequest` owns what every request shares (pending, answered, expired and
 * cancelled states, the one-line summary, the expiry, Skip, errors). A kind
 * adds only its body: what is asked and how it is answered. A new kind is one
 * `registerHitlKind` call with a matching server request kind.
 *
 * Studio agents never ask for approval, so "approval" has no body: an approval
 * stored by an earlier version replays as a settled line, never answerable.
 */
import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";
import { ListChecks, MessageCircleQuestion, SlidersHorizontal } from "lucide-react";
import { ChoiceBody } from "./choice-request";
import { FormBody } from "./form-request";
import { QuestionBody } from "./question-request";
import type { HitlAnswerBody, HitlRequestSpec } from "./types";

export type HitlBodyProps<R extends HitlRequestSpec = HitlRequestSpec> = {
  request: R;
  /** Send the answer; rejects with a message to show when the server refuses it. */
  submit: (body: HitlAnswerBody) => Promise<void>;
  /** An answer is on its way. */
  busy: boolean;
  /** The id of the card's title, for labelling the controls. */
  titleId: string;
};

export type HitlKindDefinition<R extends HitlRequestSpec = HitlRequestSpec> = {
  kind: R["kind"];
  /** What the card is, in a few words ("Question", "Approval needed"). */
  label: string;
  Icon: LucideIcon;
  Body: ComponentType<HitlBodyProps<R>>;
  /** The user may skip it; the agent then states its assumption. */
  skippable: boolean;
};

const kinds = new Map<string, HitlKindDefinition>();

export function registerHitlKind<R extends HitlRequestSpec>(definition: HitlKindDefinition<R>): void {
  kinds.set(definition.kind, definition as unknown as HitlKindDefinition);
}

export function hitlKind(kind: string): HitlKindDefinition | undefined {
  return kinds.get(kind);
}

registerHitlKind({ kind: "question", label: "Question", Icon: MessageCircleQuestion, Body: QuestionBody, skippable: true });
registerHitlKind({ kind: "form", label: "Details needed", Icon: SlidersHorizontal, Body: FormBody, skippable: true });
registerHitlKind({ kind: "choice", label: "Choose one", Icon: ListChecks, Body: ChoiceBody, skippable: true });
