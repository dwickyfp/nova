import { useCallback, useEffect, useRef, useState } from "react";
import type { TurnContext } from "./stream-client";
import type { AssistantEvent } from "./types";
import type { AttachedQuery } from "./query-attach";
import type { UiAction } from "./use-assistant-turn";
import {
  transcriptReducer,
  type TranscriptMessage,
} from "./use-assistant-transcript";
import {
  useAssistantTurn,
  type AssistantTurnSnapshot,
} from "./use-assistant-turn";

export type AssistantConversationOptions = {
  /**
   * Resolves the thread for the current binding, creating one on first use.
   * Returning null aborts the turn (for example, no file is open).
   */
  ensureThread: () => Promise<string | null>;
  /** Active worksheet context for the next turn. */
  context: TurnContext;
  getAppContext?: () => TurnContext["appContext"];
  onClientAction?: (action: Extract<AssistantEvent, { type: "client_action" }>, threadId: string, turnContext?: TurnContext["appContext"]) => Promise<void>;
  /**
   * Identity of the current conversation binding. Each retained key keeps its
   * own transcript and thread, so returning to it restores that conversation.
   */
  bindingKey: string | null;
  /**
   * Whether the conversation for a key should be kept when the binding moves
   * away from it. Defaults to false: the conversation is closed on leave, which
   * revokes its grant and discards its transcript, matching a workspace file
   * that is no longer open. Return true for a long-lived surface such as the
   * global panel's conversation, which must survive page navigation.
   */
  retainOnLeave?: (key: string | null) => boolean;
  onError?: (message: string) => void;
  /**
   * Called when a completed turn proposes a SQL rewrite for a single attached
   * query. Forwarded to the turn driver; the workspace owns what to do with it.
   */
  onProposedRewrite?: (input: {
    attachment: AttachedQuery;
    sql: string;
    messageId: string;
  }) => void;
  canApproveUiAction?: (action: UiAction) => boolean;
  onUiActionCompleted?: (action: UiAction) => void;
};

type ConversationStore = {
  messages: Record<string, TranscriptMessage[]>;
  turns: Record<string, AssistantTurnSnapshot>;
};

const EMPTY_TURNS: AssistantTurnSnapshot = {
  threadId: null,
  grantActive: false,
};

/**
 * The store-agnostic seam between a conversation surface and the turn driver.
 * It owns the binding lifecycle: each retained `bindingKey` gets its own
 * transcript and thread, so returning to it restores that conversation rather
 * than starting an empty one. Keys that are not retained are closed when the
 * binding leaves them, so a workspace file still revokes its grant and starts
 * fresh for the next file. Thread creation and turn context stay with the
 * caller, so the same hook backs both the workspace file binding and the
 * global panel.
 */
export function useAssistantConversation({
  ensureThread,
  context,
  getAppContext,
  onClientAction,
  bindingKey,
  retainOnLeave,
  onError,
  onProposedRewrite,
  canApproveUiAction,
  onUiActionCompleted,
}: AssistantConversationOptions) {
  const [store, setStore] = useState<ConversationStore>({
    messages: {},
    turns: {},
  });
  const keyRef = useRef(bindingKey);
  const storeRef = useRef(store);
  storeRef.current = store;
  const retainRef = useRef(retainOnLeave);
  retainRef.current = retainOnLeave;
  const binding = bindingKey ?? "";
  const messages = store.messages[binding] ?? [];

  const transcript = {
    messages,
    addUserMessage: useCallback(
      (content: string, attachments?: AttachedQuery[], displayText?: string) =>
        setStore((prev) => ({
          ...prev,
          messages: {
            ...prev.messages,
            [binding]: transcriptReducer(prev.messages[binding] ?? [], {
              type: "user_message",
              content,
              attachments,
              displayText,
            }),
          },
        })),
      [binding],
    ),
    applyEvent: useCallback(
      (event: AssistantEvent) =>
        setStore((prev) => ({
          ...prev,
          messages: {
            ...prev.messages,
            [binding]: transcriptReducer(prev.messages[binding] ?? [], {
              type: "event",
              event,
            }),
          },
        })),
      [binding],
    ),
    markCancelled: useCallback(
      () =>
        setStore((prev) => ({
          ...prev,
          messages: {
            ...prev.messages,
            [binding]: transcriptReducer(prev.messages[binding] ?? [], {
              type: "cancelled",
            }),
          },
        })),
      [binding],
    ),
    reset: useCallback(
      () =>
        setStore((prev) => ({
          ...prev,
          messages: { ...prev.messages, [binding]: [] },
        })),
      [binding],
    ),
    replace: useCallback(
      (next: TranscriptMessage[]) =>
        setStore((prev) => ({
          ...prev,
          messages: { ...prev.messages, [binding]: next },
        })),
      [binding],
    ),
  };

  const assistant = useAssistantTurn({
    ensureThread,
    context,
    getAppContext,
    onClientAction,
    onError,
    onProposedRewrite,
    canApproveUiAction,
    onUiActionCompleted,
    transcript,
  });
  const { snapshot, restore, resetPermissions } = assistant;
  const snapshotRef = useRef(snapshot);
  snapshotRef.current = snapshot;
  const restoreRef = useRef(restore);
  restoreRef.current = restore;
  const resetGrantRef = useRef(resetPermissions);
  resetGrantRef.current = resetPermissions;

  useEffect(() => {
    const previous = keyRef.current;
    if (previous === bindingKey) return;
    keyRef.current = bindingKey;

    const saved = storeRef.current;
    const leaving = previous ?? "";
    const leaveSnapshot = snapshotRef.current();
    const retained = retainRef.current?.(previous) ?? false;

    if (retained) {
      setStore({
        messages: saved.messages,
        turns: { ...saved.turns, [leaving]: leaveSnapshot },
      });
      restoreRef.current(saved.turns[bindingKey ?? ""] ?? EMPTY_TURNS);
      return;
    }

    // Closing the outgoing conversation: drop its transcript and, when it held
    // a grant, revoke it. The revoke runs before the restore so it targets the
    // outgoing thread, not the one being restored. Fire-and-forget, mirroring
    // the workspace behaviour.
    const nextMessages = { ...saved.messages };
    delete nextMessages[leaving];
    const nextTurns = { ...saved.turns };
    delete nextTurns[leaving];
    if (leaveSnapshot.grantActive) {
      void resetGrantRef.current();
    }
    setStore({ messages: nextMessages, turns: nextTurns });
    restoreRef.current(saved.turns[bindingKey ?? ""] ?? EMPTY_TURNS);
  }, [bindingKey]);

  return assistant;
}
