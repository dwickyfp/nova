import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from 'react'
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
  /** Applies the persisted `assistant_collapsed` once, on first tree load. */
  initialiseOpen: (tree: { assistant_collapsed?: boolean }) => void
  /** Reads the value to persist, translating the panel polarity once. */
  collapsedToPersist: () => boolean
  /** Registers the active surface binding; call with null to clear it. */
  setBinding: (binding: AssistantBinding | null) => void
  conversation: ReturnType<typeof useAssistantConversation>
}

const AssistantContext = createContext<AssistantContextType | null>(null)

const DEFAULT_CONTEXT: TurnContext = { database: null, schema: null, role: null }

export function AssistantProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpenState] = useState(false)
  const [binding, setBindingState] = useState<AssistantBinding | null>(null)
  const initialisedRef = useRef(false)

  const noopEnsureThread = useCallback(async () => null, [])

  const ensureThread = binding?.ensureThread ?? noopEnsureThread
  const context = binding?.context ?? DEFAULT_CONTEXT
  const bindingKey = binding?.key ?? null
  const onError = binding?.onError

  const conversation = useAssistantConversation({
    ensureThread,
    context,
    bindingKey,
    onError,
  })

  const setOpen = useCallback((next: boolean) => setOpenState(next), [])
  const toggle = useCallback(() => setOpenState((prev) => !prev), [])

  const initialiseOpen = useCallback((tree: { assistant_collapsed?: boolean }) => {
    if (initialisedRef.current) return
    initialisedRef.current = true
    setOpenState(initialAssistantOpen(tree))
  }, [])

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
      initialiseOpen,
      collapsedToPersist,
      setBinding,
      conversation,
    }),
    [open, setOpen, toggle, initialiseOpen, collapsedToPersist, setBinding, conversation]
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
