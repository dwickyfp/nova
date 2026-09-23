import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { getCookie, setCookie } from "@/lib/cookies";
import { useAuthStore } from "@/stores/auth-store";
import type { WorkspaceTreeResponse } from "@/features/workspaces/types";
import { createThread, listThreads } from "./thread-client";
import type { TurnContext } from "./stream-client";
import { useAssistantConversation } from "./use-assistant-conversation";
import type { ApprovalMode, UiAction } from "./use-assistant-turn";
import type { AssistantPanelProps } from "./assistant-panel";
import {
  nextAttachmentId,
  type AttachContext,
  type AttachedQuery,
  type ProposedRewrite,
} from "./query-attach";
import {
  ASSISTANT_OFFSET_Y_COOKIE,
  ASSISTANT_WIDTH_COOKIE,
  assistantCollapsedToPersist,
  clampAssistantWidth,
  initialAssistantOpen,
  parseAssistantOffsetY,
  parseAssistantWidth,
} from "./assistant-panel-state";

type AssistantBinding = {
  /** Identity of the current conversation binding; a change starts a new one. */
  key: string | null;
  context: TurnContext;
  ensureThread: () => Promise<string | null>;
  /** Surfaces binding-specific errors, such as no open file. */
  onError?: (message: string) => void;
  /**
   * Called when a completed turn answered a single attached query with a SQL
   * block. The binding owner (the workspace) turns it into an editor diff.
   */
  onProposedRewrite?: (input: {
    attachment: AttachedQuery;
    sql: string;
    messageId: string;
  }) => void;
  canApproveUiAction?: (action: UiAction) => boolean;
  onUiActionCompleted?: (action: UiAction) => void;
  /** Label the header shows for this binding, e.g. the open worksheet's name. */
  title?: string;
};

/** The model pinned for turns, or null to let the backend choose. */
export type SelectedModel = { providerId: string; model: string } | null;

type AssistantContextType = {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  /** Reads the value to persist, translating the panel polarity once. */
  collapsedToPersist: () => boolean;
  /** Registers the active surface binding; call with null to clear it. */
  setBinding: (binding: AssistantBinding | null) => void;
  conversation: ReturnType<typeof useAssistantConversation>;
  /**
   * Sends a message with the pending attachments, then clears them. This is
   * what the panel's composer calls; the raw conversation sender stays private
   * so attachments can never be dropped silently.
   */
  sendMessage: (message: string) => Promise<void>;
  /** Queries attached to the next message, shown as badges above the composer. */
  attachments: AttachedQuery[];
  /** Attaches a workspace selection; returns the created attachment. */
  attachQuery: (context: AttachContext) => AttachedQuery;
  /** Removes one attached query by id. */
  removeAttachment: (id: string) => void;
  /** Clears every attached query (after a send, or on a new chat). */
  clearAttachments: () => void;
  /**
   * The rewrite the assistant last proposed, awaiting Approve/Deny in the
   * workspace editor. Null when there is nothing to review.
   */
  proposedRewrite: ProposedRewrite | null;
  /** Publishes a proposed rewrite (called by the turn driver). */
  setProposedRewrite: (rewrite: ProposedRewrite | null) => void;
  /** Model pinned for the next turn; persisted across the session. */
  selectedModel: SelectedModel;
  setSelectedModel: (model: SelectedModel) => void;
  /** How the next read-only query is approved: a card, or the conversation grant. */
  approvalMode: ApprovalMode;
  setApprovalMode: (mode: ApprovalMode) => Promise<void>;
  /** True while an approval-mode change is being persisted. */
  settlingApprovalMode: boolean;
  /** True while an existing thread's messages are being fetched. */
  loadingThread: boolean;
  /** Switches the conversation to an existing thread. */
  openThread: (threadId: string) => Promise<void>;
  /** Clears the conversation so the next message starts a fresh thread. */
  newChat: () => void;
  /** Panel width in px, clamped to the allowed range; persisted in a cookie. */
  width: number;
  setWidth: (width: number) => void;
  /**
   * Vertical offset in px for the floating toggle: 0 is the default lower-right
   * position, positive is upward. Persisted in a cookie like the width.
   */
  offsetY: number;
  setOffsetY: (offsetY: number) => void;
  /**
   * The database/schema/role the panel's Run buttons execute against — the same
   * context a turn is sent with, so a card runs where the conversation is.
   */
  activeContext: TurnContext;
  /** Label the header shows, e.g. the open worksheet's name; "Nove" when none. */
  headerTitle?: string;
};

const AssistantContext = createContext<AssistantContextType | null>(null);

const EMPTY_CONTEXT: TurnContext = { database: null, schema: null, role: null };

/**
 * Stable identity for the conversation that is not bound to a workspace file.
 * It must not be `null`, because the workspace uses `null` for "no file open";
 * a sentinel keeps the global conversation distinct from that state and
 * unbroken as the user moves between pages.
 */
export const GLOBAL_ASSISTANT_BINDING_KEY = "__global__";

export function AssistantProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [open, setOpenState] = useState(false);
  const [binding, setBindingState] = useState<AssistantBinding | null>(null);
  const [selectedModel, setSelectedModel] = useState<SelectedModel>(null);
  // Read synchronously on first render so the panel does not flash at the
  // default width before the cookie is applied.
  const [width, setWidthState] = useState(() =>
    parseAssistantWidth(getCookie(ASSISTANT_WIDTH_COOKIE)),
  );
  // Same synchronous-read trick as the width: the toggle mounts at its stored
  // offset on the first paint instead of jumping from the default.
  const [offsetY, setOffsetYState] = useState(() =>
    parseAssistantOffsetY(getCookie(ASSISTANT_OFFSET_Y_COOKIE)),
  );
  // The persisted value arrives with the tree after first paint. Apply it once
  // so a later refetch cannot clobber a toggle the user just made.
  const initialisedRef = useRef(false);

  // Same key and fetcher as WorkspacesPage, so the two consumers share one
  // cache entry instead of issuing a second GET when both are mounted.
  const workspaceTreeQuery = useQuery<WorkspaceTreeResponse>({
    queryKey: ["workspace-tree"],
    queryFn: () => api.get<WorkspaceTreeResponse>("/workspaces/tree"),
  });

  const tree = workspaceTreeQuery.data;
  useEffect(() => {
    if (!tree || initialisedRef.current) return;
    initialisedRef.current = true;
    setOpenState(initialAssistantOpen(tree));
  }, [tree]);

  const treeContext = tree?.defaults;

  /**
   * The binding used whenever no workspace file is bound, so the panel works on
   * every route. Threads do not require a file, so this creates a file-less
   * thread with the tree's default database/schema/role. The turn driver caches
   * the thread for the life of the conversation and only calls `ensureThread`
   * when none is set, so a new conversation gets a new thread without this
   * closure having to hold one across binding changes.
   */
  const defaultBinding = useMemo<AssistantBinding>(
    () => ({
      key: GLOBAL_ASSISTANT_BINDING_KEY,
      context: treeContext ?? EMPTY_CONTEXT,
      ensureThread: () => createThread(null).then((thread) => thread.thread_id),
    }),
    [treeContext],
  );

  const activeBinding = binding ?? defaultBinding;
  const onUiActionCompleted = useCallback(
    (action: UiAction) => {
      if (action.method !== "GET") void queryClient.invalidateQueries();
      activeBinding.onUiActionCompleted?.(action);
    },
    [activeBinding, queryClient],
  );

  // The selected model is layered onto the binding's own context so the turn
  // driver reads one context object; it does not participate in the binding
  // identity, so changing the model must not start a new conversation.
  const context = useMemo<TurnContext>(
    () => ({
      ...activeBinding.context,
      model: selectedModel?.model ?? null,
      providerId: selectedModel?.providerId ?? null,
    }),
    [activeBinding.context, selectedModel],
  );

  const conversation = useAssistantConversation({
    ensureThread: activeBinding.ensureThread,
    context,
    bindingKey: activeBinding.key,
    // The global conversation survives navigation; a workspace file does not,
    // so leaving it still revokes its grant and starts the next file fresh.
    retainOnLeave: (key) => key === GLOBAL_ASSISTANT_BINDING_KEY,
    onError: activeBinding.onError,
    onProposedRewrite: activeBinding.onProposedRewrite,
    canApproveUiAction: activeBinding.canApproveUiAction,
    onUiActionCompleted,
  });

  /**
   * Attachments belong to the surface that produced them, so they are cleared
   * whenever the binding changes: a selection from a closed file must not leak
   * into another file's conversation.
   */
  const [attachments, setAttachments] = useState<AttachedQuery[]>([]);
  const [proposedRewrite, setProposedRewriteState] =
    useState<ProposedRewrite | null>(null);
  const bindingKey = activeBinding.key;
  useEffect(() => {
    setAttachments([]);
    setProposedRewriteState(null);
  }, [bindingKey]);

  const attachQuery = useCallback((attachContext: AttachContext) => {
    const attachment: AttachedQuery = {
      ...attachContext,
      id: nextAttachmentId(),
      createdAt: Date.now(),
    };
    setAttachments((prev) => [...prev, attachment]);
    return attachment;
  }, []);

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((attachment) => attachment.id !== id));
  }, []);

  const clearAttachments = useCallback(() => setAttachments([]), []);

  const setProposedRewrite = useCallback((rewrite: ProposedRewrite | null) => {
    setProposedRewriteState(rewrite);
  }, []);

  /**
   * Sends the composer's message with the pending attachments, then clears
   * them so they apply to exactly one turn. The wrapped sender is what the
   * panel receives, keeping the attachment rule in one place.
   */
  const sendMessage = useCallback(
    (message: string) => {
      const pending = attachments;
      setAttachments([]);
      return conversation.sendMessage(message, pending);
    },
    [attachments, conversation],
  );

  const openThread = useCallback(
    (threadId: string) => conversation.loadThread(threadId),
    [conversation],
  );
  const newChat = useCallback(() => {
    setAttachments([]);
    setProposedRewriteState(null);
    conversation.startNewThread();
  }, [conversation]);

  const setOpen = useCallback((next: boolean) => setOpenState(next), []);
  const toggle = useCallback(() => setOpenState((prev) => !prev), []);

  const setWidth = useCallback((next: number) => {
    const clamped = clampAssistantWidth(next);
    setWidthState(clamped);
    setCookie(ASSISTANT_WIDTH_COOKIE, String(clamped));
  }, []);

  // The toggle clamps against its own measured rect before calling this, so
  // the provider only persists what it is given.
  const setOffsetY = useCallback((next: number) => {
    const rounded = Math.round(next);
    setOffsetYState(rounded);
    setCookie(ASSISTANT_OFFSET_Y_COOKIE, String(rounded));
  }, []);

  const collapsedToPersist = useCallback(
    () => assistantCollapsedToPersist(open),
    [open],
  );

  const setBinding = useCallback((next: AssistantBinding | null) => {
    setBindingState((prev) => {
      if (prev === null && next === null) return prev;
      if (
        prev &&
        next &&
        prev.key === next.key &&
        prev.context === next.context
      )
        return prev;
      return next;
    });
  }, []);

  const value = useMemo<AssistantContextType>(
    () => ({
      open,
      setOpen,
      toggle,
      collapsedToPersist,
      setBinding,
      conversation,
      sendMessage,
      attachments,
      attachQuery,
      removeAttachment,
      clearAttachments,
      proposedRewrite,
      setProposedRewrite,
      selectedModel,
      setSelectedModel,
      approvalMode: conversation.approvalMode,
      setApprovalMode: conversation.setApprovalMode,
      settlingApprovalMode: conversation.settlingGrant,
      loadingThread: conversation.loadingThread,
      openThread,
      newChat,
      width,
      setWidth,
      offsetY,
      setOffsetY,
      activeContext: context,
      headerTitle: activeBinding.title,
    }),
    [
      open,
      setOpen,
      toggle,
      collapsedToPersist,
      setBinding,
      conversation,
      sendMessage,
      attachments,
      attachQuery,
      removeAttachment,
      clearAttachments,
      proposedRewrite,
      setProposedRewrite,
      selectedModel,
      openThread,
      newChat,
      width,
      setWidth,
      offsetY,
      setOffsetY,
      context,
      activeBinding.title,
    ],
  );

  return <AssistantContext value={value}>{children}</AssistantContext>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAssistant(): AssistantContextType {
  const context = useContext(AssistantContext);
  if (!context) {
    throw new Error("useAssistant must be used within an AssistantProvider");
  }
  return context;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAssistantPanelProps(): AssistantPanelProps {
  const {
    open,
    setOpen,
    conversation,
    attachments,
    removeAttachment,
    sendMessage,
    selectedModel,
    setSelectedModel,
    loadingThread,
    openThread,
    newChat,
    width,
    setWidth,
    offsetY,
    setOffsetY,
    activeContext,
    headerTitle,
  } = useAssistant();
  // The greeting addresses the signed-in user by name; until `/auth/me` lands
  // the store is empty and the empty state falls back to a neutral salutation.
  const userName = useAuthStore((state) => state.auth.user?.username ?? null);
  // Same key and fetcher as the header's history popover, so both read one
  // cached list instead of issuing a second GET when the panel is empty.
  const threadsQuery = useQuery({
    queryKey: ["assistant-threads"],
    queryFn: listThreads,
    staleTime: 0,
  });
  const recentThreads = useMemo(
    () => (threadsQuery.data?.threads ?? []).slice(0, 4),
    [threadsQuery.data],
  );
  return useMemo(
    () => ({
      open,
      onOpenChange: setOpen,
      title: headerTitle,
      userName,
      recentThreads,
      messages: conversation.messages,
      onSendMessage: sendMessage,
      attachments,
      onRemoveAttachment: removeAttachment,
      onDecide: conversation.decide,
      decidingToolCallId: conversation.decidingToolCallId,
      streaming: conversation.streaming,
      onStop: conversation.stop,
      statusMessage: conversation.statusMessage,
      selectedModel,
      onSelectModel: setSelectedModel,
      approvalMode: conversation.approvalMode,
      onSelectApprovalMode: conversation.setApprovalMode,
      settlingApprovalMode: conversation.settlingGrant,
      onNewChat: newChat,
      onOpenThread: openThread,
      activeThreadId: conversation.threadId,
      loadingThread,
      width,
      onResize: setWidth,
      offsetY,
      onOffsetYChange: setOffsetY,
      activeContext,
    }),
    [
      open,
      setOpen,
      conversation,
      attachments,
      removeAttachment,
      sendMessage,
      selectedModel,
      setSelectedModel,
      loadingThread,
      openThread,
      newChat,
      width,
      setWidth,
      offsetY,
      setOffsetY,
      activeContext,
      headerTitle,
      userName,
      recentThreads,
    ],
  );
}
