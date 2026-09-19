import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import type { WorkspaceTreeResponse } from '@/features/workspaces/types'
import { createThread } from './thread-client'
import type { TurnContext } from './stream-client'
import { useAssistantConversation } from './use-assistant-conversation'
import type { AssistantPanelProps } from './assistant-panel'
import { initialAssistantOpen, assistantCollapsedToPersist } from './assistant-panel-state'

type AssistantBinding = {
  /** Identity of the current conversation binding; a change starts a new one. */
  key: string | null
  context: TurnContext
  ensureThread: () => Promise<string | null>
  /** Surfaces binding-specific errors, such as no open file. */
  onError?: (message: string) => void
}

type AssistantContextType = {
  open: boolean
  setOpen: (open: boolean) => void
  toggle: () => void
  /** Reads the value to persist, translating the panel polarity once. */
  collapsedToPersist: () => boolean
  /** Registers the active surface binding; call with null to clear it. */
  setBinding: (binding: AssistantBinding | null) => void
  conversation: ReturnType<typeof useAssistantConversation>
}

const AssistantContext = createContext<AssistantContextType | null>(null)

const EMPTY_CONTEXT: TurnContext = { database: null, schema: null, role: null }

/**
 * Stable identity for the conversation that is not bound to a workspace file.
 * It must not be `null`, because the workspace uses `null` for "no file open";
 * a sentinel keeps the global conversation distinct from that state and
 * unbroken as the user moves between pages.
 */
export const GLOBAL_ASSISTANT_BINDING_KEY = '__global__'

export function AssistantProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpenState] = useState(false)
  const [binding, setBindingState] = useState<AssistantBinding | null>(null)
  // The persisted value arrives with the tree after first paint. Apply it once
  // so a later refetch cannot clobber a toggle the user just made.
  const initialisedRef = useRef(false)

  // Same key and fetcher as WorkspacesPage, so the two consumers share one
  // cache entry instead of issuing a second GET when both are mounted.
  const workspaceTreeQuery = useQuery<WorkspaceTreeResponse>({
    queryKey: ['workspace-tree'],
    queryFn: () => api.get<WorkspaceTreeResponse>('/workspaces/tree'),
  })

  const tree = workspaceTreeQuery.data
  useEffect(() => {
    if (!tree || initialisedRef.current) return
    initialisedRef.current = true
    setOpenState(initialAssistantOpen(tree))
  }, [tree])

  const treeContext = tree?.defaults

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
    [treeContext]
  )

  const activeBinding = binding ?? defaultBinding

  const conversation = useAssistantConversation({
    ensureThread: activeBinding.ensureThread,
    context: activeBinding.context,
    bindingKey: activeBinding.key,
    // The global conversation survives navigation; a workspace file does not,
    // so leaving it still revokes its grant and starts the next file fresh.
    retainOnLeave: (key) => key === GLOBAL_ASSISTANT_BINDING_KEY,
    onError: activeBinding.onError,
  })

  const setOpen = useCallback((next: boolean) => setOpenState(next), [])
  const toggle = useCallback(() => setOpenState((prev) => !prev), [])

  const collapsedToPersist = useCallback(() => assistantCollapsedToPersist(open), [open])

  const setBinding = useCallback((next: AssistantBinding | null) => {
    setBindingState((prev) => {
      if (prev === null && next === null) return prev
      if (prev && next && prev.key === next.key && prev.context === next.context) return prev
      return next
    })
  }, [])

  const value = useMemo<AssistantContextType>(
    () => ({
      open,
      setOpen,
      toggle,
      collapsedToPersist,
      setBinding,
      conversation,
    }),
    [open, setOpen, toggle, collapsedToPersist, setBinding, conversation]
  )

  return <AssistantContext value={value}>{children}</AssistantContext>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAssistant(): AssistantContextType {
  const context = useContext(AssistantContext)
  if (!context) {
    throw new Error('useAssistant must be used within an AssistantProvider')
  }
  return context
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAssistantPanelProps(): AssistantPanelProps {
  const { open, setOpen, conversation } = useAssistant()
  return useMemo(
    () => ({
      open,
      onOpenChange: setOpen,
      messages: conversation.messages,
      onSendMessage: conversation.sendMessage,
      onDecide: conversation.decide,
      decidingToolCallId: conversation.decidingToolCallId,
      streaming: conversation.streaming,
      onStop: conversation.stop,
      statusMessage: conversation.statusMessage,
      grantActive: conversation.grantActive,
      onResetPermissions: conversation.resetPermissions,
      resettingPermissions: conversation.resettingGrant,
    }),
    [open, setOpen, conversation]
  )
}
