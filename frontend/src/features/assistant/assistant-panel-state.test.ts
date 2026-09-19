import { describe, expect, it } from 'vitest'
import { assistantCollapsedToPersist, initialAssistantOpen } from './assistant-panel-state'

describe('assistant panel persistence polarity', () => {
  it('round-trips an open panel through the persisted collapsed field', () => {
    const persisted = assistantCollapsedToPersist(true)
    expect(persisted).toBe(false)
    expect(initialAssistantOpen({ assistant_collapsed: persisted })).toBe(true)
  })

  it('round-trips a closed panel through the persisted collapsed field', () => {
    const persisted = assistantCollapsedToPersist(false)
    expect(persisted).toBe(true)
    expect(initialAssistantOpen({ assistant_collapsed: persisted })).toBe(false)
  })

  it('defaults to closed when the backend does not send the field yet', () => {
    expect(initialAssistantOpen({})).toBe(false)
  })
})
