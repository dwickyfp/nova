import { AssistantPanel } from './assistant-panel'
import { AssistantToggle } from './assistant-toggle'
import { useAssistant, useAssistantPanelProps } from './assistant-provider'

/**
 * The global assistant surface: the panel plus its persistent toggle. Mounted
 * once in the authenticated layout so every route shares one conversation and
 * one open/closed state. On `md` and up the panel is an inline flex sibling of
 * the page content; below `md` it becomes a `Sheet` while the same toggle stays
 * as the trigger.
 */
export function AssistantDock() {
  const { open, toggle } = useAssistant()
  const panelProps = useAssistantPanelProps()

  return (
    <>
      <AssistantPanel {...panelProps} />
      <AssistantToggle open={open} onToggle={toggle} />
    </>
  )
}
