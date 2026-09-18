import { describe, expect, it } from 'vitest'
import { initialAssistantOpen } from './assistant-panel-state'

describe('initialAssistantOpen', () => {
  it('restores an open panel from persisted workspace state', () => {
    expect(initialAssistantOpen({ assistant_collapsed: false })).toBe(true)
  })

  it('restores a closed panel from persisted workspace state', () => {
    expect(initialAssistantOpen({ assistant_collapsed: true })).toBe(false)
  })

  it('defaults to closed when the backend does not send the field yet', () => {
    expect(initialAssistantOpen({})).toBe(false)
  })
})
