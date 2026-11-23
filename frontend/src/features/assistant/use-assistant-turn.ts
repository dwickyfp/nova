import { useCallback, useRef, useState } from "react";
import {
  streamAssistantTurn,
  decideToolCall,
  type SentFile,
  type TurnContext,
} from "./stream-client";
import { getThread } from "./thread-client";
import type { ToolCallDecision } from "./tool-call-card";
import type { AssistantEvent } from "./types";
import {
  restoredMessage,
  useAssistantTranscript,
  type TranscriptMessage,
} from "./use-assistant-transcript";
import {
  extractSqlCodeBlock,
  formatAttachmentsForPrompt,
  type AttachedQuery,
} from "./query-attach";

export type UiAction = { method: string; path: string };

export function uiActionFromPreview(
  toolName: string,
  preview: string,
): UiAction | null {
  if (toolName !== "call_ui_operation") return null;
  const match = /^(GET|POST|PUT|PATCH|DELETE) (\/api\/v1\/[^\s]+)/.exec(
    preview,
  );
  return match ? { method: match[1], path: match[2] } : null;
}

const STATUS_TEXT: Partial<Record<AssistantEvent["type"], string>> = {
  tool_call: "The assistant is waiting for your approval.",
  done: "The assistant finished responding.",
  error: "The assistant ran into an error.",
};

export type AssistantTurnOptions = {
  /**
   * Resolves the thread to send into, creating one on first use. Returning
   * null aborts the turn (for example, no file is open).
   */
  ensureThread: () => Promise<string | null>;
  /** Active worksheet context for this turn. */
  context?: TurnContext;
  getAppContext?: () => TurnContext["appContext"];
  onClientAction?: (action: Extract<AssistantEvent, { type: "client_action" }>, threadId: string, turnContext?: TurnContext["appContext"]) => Promise<void>;
  onError?: (message: string) => void;
  /**
   * Called when a completed turn ends with a single attached query and the
   * answer contains a SQL block: the workspace turns that block into an inline
   * before/after diff. Not called for plain chat answers or multi-attachment
   * turns, where a rewrite has no single target.
   */
  onProposedRewrite?: (input: {
    attachment: AttachedQuery;
    sql: string;
    messageId: string;
  }) => void;
  canApproveUiAction?: (action: UiAction) => boolean;
  onUiActionCompleted?: (action: UiAction) => void;
  /**
   * Transcript store to drive. The provider injects one per conversation
   * binding so switching bindings can restore a previous conversation's
   * messages; standalone callers get their own.
   */
  transcript?: ReturnType<typeof useAssistantTranscript>;
};

/** The parts of a conversation that must survive a binding switch. */
export type AssistantTurnSnapshot = {
  threadId: string | null;
  olderCursor?: string | null;
};

/**
 * Drives one assistant turn: user message, SSE events, stop, and consent.
 * The panel renders `messages` and passes `onDecide`; the workspace supplies
 * `ensureThread` so thread creation stays bound to the active file.
 */
export function useAssistantTurn({
  ensureThread,
  context,
  getAppContext,
  onClientAction,
  onError,
  onProposedRewrite,
  canApproveUiAction,
  onUiActionCompleted,
  transcript: injectedTranscript,
}: AssistantTurnOptions) {
  const ownTranscript = useAssistantTranscript();
  const transcript = injectedTranscript ?? ownTranscript;
  const [streaming, setStreaming] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [decidingToolCallId, setDecidingToolCallId] = useState<string | null>(
    null,
  );
  const [threadId, setThreadId] = useState<string | null>(null);
  const [loadingThread, setLoadingThread] = useState(false);
  const [olderCursor, setOlderCursor] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const historyRequestRef = useRef(0);
  const transcriptRef = useRef(transcript);
  transcriptRef.current = transcript;
  const abortRef = useRef<AbortController | null>(null);
  const uiActionsRef = useRef(new Map<string, UiAction>());

  const sendMessage = useCallback(
    async (
      message: string,
      attachments: AttachedQuery[] = [],
      files: SentFile[] = [],
    ) => {
      if (streaming) return;
      const thread = threadId ?? (await ensureThread());
      if (!thread) {
        onError?.("Open or create a SQL file before asking the assistant");
        return;
      }
      if (thread !== threadId) setThreadId(thread);

      const prompt = `${formatAttachmentsForPrompt(attachments)}${message}`;
      transcript.addUserMessage(
        prompt, attachments, message, files.map((file) => file.name),
      );
      const controller = new AbortController();
      abortRef.current = controller;
      uiActionsRef.current.clear();
      setStreaming(true);
      setStatusMessage("The assistant is responding.");
      // Accumulated answer text, so a completed turn can be scanned for a
      // proposed SQL rewrite without reading back through transcript state.
      let answer = "";
      let answerMessageId = "";
      const turnAppContext = getAppContext?.() ?? context?.appContext;
      try {
        await streamAssistantTurn(thread, prompt, {
          signal: controller.signal,
          database: context?.database,
          schema: context?.schema,
          role: context?.role,
          model: context?.model,
           providerId: context?.providerId,
           appContext: turnAppContext,
           files,
           onEvent: (event) => {
             transcript.applyEvent(event);
             if (event.type === "client_action") {
               void onClientAction?.(event, thread, turnAppContext).catch((error) => {
                 onError?.(error instanceof Error ? error.message : "The page action failed.");
               });
             }
            if (event.type === "tool_call") {
              const action = uiActionFromPreview(
                event.payload.tool_name,
                event.payload.sql_preview,
              );
              if (action)
                uiActionsRef.current.set(event.payload.tool_call_id, action);
            }
            if (event.type === "tool_status") {
              const action = uiActionsRef.current.get(event.tool_call_id);
              if (action && event.status === "done")
                onUiActionCompleted?.(action);
              if (event.status !== "running" && event.status !== "pending") {
                uiActionsRef.current.delete(event.tool_call_id);
              }
            }
            if (event.type === "role_changed") answer = "";
            if (event.type === "text_delta") answer += event.text;
            if (event.type === "done") answerMessageId = event.message_id;
            const status = STATUS_TEXT[event.type];
            if (status) setStatusMessage(status);
          },
        });
        if (controller.signal.aborted) {
          transcript.markCancelled();
          setStatusMessage("Response stopped.");
        } else if (attachments.length === 1) {
          const code = extractSqlCodeBlock(answer);
          if (code) {
            onProposedRewrite?.({
              attachment: attachments[0],
              sql: code,
              messageId: answerMessageId,
            });
          }
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          transcript.markCancelled();
          setStatusMessage("Response stopped.");
        } else {
          const message =
            error instanceof Error ? error.message : "The assistant failed";
          transcript.applyEvent({ type: "error", code: "transport", message });
          onError?.(message);
        }
      } finally {
        abortRef.current = null;
        setStreaming(false);
      }
    },
    [
      context?.database,
      context?.model,
      context?.providerId,
      context?.role,
      context?.schema,
      ensureThread,
      onError,
      getAppContext,
      onClientAction,
      onProposedRewrite,
      onUiActionCompleted,
      streaming,
      threadId,
      transcript,
    ],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  /**
   * Switches the conversation to an existing thread: adopts its id, replaces
   * the transcript with the stored messages, and continues in it. Any in-flight
   * stream is aborted first, because the new transcript must not be appended to
   * by a turn belonging to the previous thread.
   */
  const loadThread = useCallback(
    async (targetThreadId: string) => {
      const request = ++historyRequestRef.current;
      setLoadingOlder(false);
      abortRef.current?.abort();
      abortRef.current = null;
      setStreaming(false);
      setStatusMessage(null);
      setDecidingToolCallId(null);
      setLoadingThread(true);
      try {
        const detail = await getThread(targetThreadId);
        if (request !== historyRequestRef.current) return;
        const restored: TranscriptMessage[] = detail.messages
          .filter((message) => message.role !== "tool")
          .map(restoredMessage);
        transcript.replace(restored);
        setOlderCursor(detail.next_cursor ?? null);
        setThreadId(detail.thread.thread_id);
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "The thread could not be opened";
        onError?.(message);
      } finally {
        if (request === historyRequestRef.current) setLoadingThread(false);
      }
    },
    [onError, transcript],
  );

  const loadOlderMessages = useCallback(async () => {
    if (!threadId || !olderCursor || loadingOlder || streaming || loadingThread) return;
    const request = historyRequestRef.current;
    setLoadingOlder(true);
    try {
      const page = await getThread(threadId, olderCursor);
      if (request !== historyRequestRef.current) return;
      const restored: TranscriptMessage[] = page.messages
        .filter((message) => message.role !== "tool")
        .map(restoredMessage);
      const current = transcriptRef.current;
      current.replace([...new Map([...restored, ...current.messages].map((m) => [m.message_id, m])).values()]);
      setOlderCursor(page.next_cursor ?? null);
    } catch {
      if (request === historyRequestRef.current) onError?.("Could not load older messages. Try again.");
    } finally {
      if (request === historyRequestRef.current) setLoadingOlder(false);
    }
  }, [threadId, olderCursor, loadingOlder, streaming, loadingThread, onError]);

  /**
   * Clears the conversation so the next message starts a fresh thread.
   */
  const startNewThread = useCallback(() => {
    historyRequestRef.current += 1;
    setOlderCursor(null);
    setLoadingOlder(false);
    setLoadingThread(false);
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setStatusMessage(null);
    setDecidingToolCallId(null);
    setThreadId(null);
    transcript.replace([]);
  }, [transcript]);

  const decide = useCallback(
    async ({
      toolCallId,
      decision,
      secureInput,
      uploadFile,
    }: ToolCallDecision) => {
      const action = uiActionsRef.current.get(toolCallId);
      if (
        decision === "approve" &&
        action &&
        canApproveUiAction?.(action) === false
      ) {
        return;
      }
      setDecidingToolCallId(toolCallId);
      try {
        await decideToolCall(toolCallId, decision, secureInput, uploadFile);
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "The decision was not applied";
        transcript.applyEvent({ type: "error", code: "consent", message });
        onError?.(message);
      } finally {
        setDecidingToolCallId(null);
      }
    },
    [canApproveUiAction, onError, transcript],
  );

  /**
   * Reads the conversation's thread binding so a caller that keeps one
   * turn per binding identity can restore it after switching away and back.
   * Aborts any in-flight stream first: a snapshot must describe a settled
   * conversation, never one mid-turn.
   */
  const snapshot = useCallback((): AssistantTurnSnapshot => {
    abortRef.current?.abort();
    abortRef.current = null;
    return { threadId, ...(olderCursor ? { olderCursor } : {}) };
  }, [threadId, olderCursor]);

  const restore = useCallback((snapshot: AssistantTurnSnapshot) => {
    historyRequestRef.current += 1;
    setOlderCursor(snapshot.olderCursor ?? null);
    setLoadingOlder(false);
    setLoadingThread(false);
    abortRef.current?.abort();
    abortRef.current = null;
    setThreadId(snapshot.threadId);
    setStreaming(false);
    setStatusMessage(null);
    setDecidingToolCallId(null);
  }, []);

  return {
    threadId,
    messages: transcript.messages,
    sendMessage,
    stop,
    decide,
    streaming,
    loadingThread,
    olderCursor,
    loadingOlder,
    loadOlderMessages,
    statusMessage,
    decidingToolCallId,
    loadThread,
    startNewThread,
    snapshot,
    restore,
  };
}
