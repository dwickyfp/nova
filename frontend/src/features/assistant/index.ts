export { AssistantPanel, type AssistantPanelProps } from "./assistant-panel";
export { AssistantToggle, type AssistantToggleProps } from "./assistant-toggle";
export { AssistantDock } from "./assistant-dock";
export {
  AssistantComposer,
  type AssistantComposerProps,
} from "./assistant-composer";
export {
  AssistantEmptyState,
  ASSISTANT_TOUR_PROMPT,
  type AssistantEmptyStateProps,
} from "./assistant-empty-state";
export { Markdown } from "./markdown";
export { CodeCard, type CodeCardProps, type CodeCardStatus } from "./code-card";
export {
  PanelResizeHandle,
  type PanelResizeHandleProps,
} from "./panel-resize-handle";
export { usePanelResize } from "./use-panel-resize";
export { ModelSelector, type ModelSelectorProps } from "./model-selector";
export { ThreadHistory, type ThreadHistoryProps } from "./thread-history";
export {
  AssistantProvider,
  useAssistant,
  useAssistantPanelProps,
  type SelectedModel,
} from "./assistant-provider";
export { useNoveSurface } from "./nove-surface-hook";
export { defineNoveCapability } from "./surface-registry";
export type {
  NoveClientCapability,
  NoveSurfaceDefinition,
  NoveSuggestedAction,
  NoveCapabilityRisk,
} from "./surface-registry";
export type { NoveAppContext, NoveSurfaceContext, NoveApplicationEvent, NoveEventInput } from "./app-context";
export {
  assistantCollapsedToPersist,
  clampAssistantWidth,
  parseAssistantWidth,
  ASSISTANT_MIN_WIDTH,
  ASSISTANT_MAX_WIDTH,
  ASSISTANT_DEFAULT_WIDTH,
  ASSISTANT_WIDTH_COOKIE,
  clampAssistantOffsetY,
  parseAssistantOffsetY,
  ASSISTANT_DEFAULT_OFFSET_Y,
  ASSISTANT_OFFSET_Y_COOKIE,
  ASSISTANT_OFFSET_Y_STEP,
  ASSISTANT_TOGGLE_REST_BOTTOM,
} from "./assistant-panel-state";
export {
  useAssistantConversation,
  type AssistantConversationOptions,
} from "./use-assistant-conversation";
export { MessageList, type MessageListProps } from "./message-list";
export {
  ToolCallCard,
  type ToolCallCardProps,
  type ToolCallDecision,
} from "./tool-call-card";
export { ActivityTrace, type ActivityTraceProps } from "./activity-trace";
export type { ActivityStep } from "./use-assistant-transcript";
export {
  parseAssistantEvent,
  readSseFrames,
  isToolCallStatus,
  isToolClassification,
} from "./events";
export {
  streamAssistantTurn,
  decideToolCall,
  toConsentPayload,
  type ConsentDecisionResponse,
} from "./stream-client";
export type { TurnContext } from "./stream-client";
export {
  createThread,
  listThreads,
  getThread,
  renameThread,
  deleteThread,
  resetGrant,
  type ThreadView,
  type ThreadListResponse,
  type ThreadDetailResponse,
  type ThreadMessageView,
} from "./thread-client";
export {
  listModelOptions,
  type ModelOption,
  type AssistantProviderRecord,
  type AssistantModel,
} from "./model-client";
export {
  useAssistantTranscript,
  transcriptReducer,
  type TranscriptMessage,
} from "./use-assistant-transcript";
export {
  useAssistantTurn,
  type AssistantTurnOptions,
} from "./use-assistant-turn";
export {
  useWorkspaceAssistant,
  type WorkspaceAssistantOptions,
} from "./use-workspace-assistant";
export { useIsNarrowForAssistant } from "./use-assistant-panel";
export {
  buildRewriteHunks,
  applyHunks,
  diffLines,
  extractSqlCodeBlock,
  formatAttachmentsForPrompt,
  nextAttachmentId,
  type AttachedQuery,
  type AttachContext,
  type ProposedRewrite,
  type RewriteHunk,
  type RewriteKind,
} from "./query-attach";
export type {
  AssistantEvent,
  AssistantMessage,
  ConsentDecision,
  ConsentDecisionPayload,
  ToolCallStatus,
  ToolCallView,
  ToolClassification,
  TurnState,
} from "./types";
