import { describe, expect, it } from 'vitest'
import { statusLabel, statusTone } from './status-tone'

describe('the standard state colours', () => {
  it('shows good, live states in green whatever their spelling', () => {
    for (const status of ['ACTIVE', 'active', 'Verified', 'connected', 'PUBLISHED', 'completed'])
      expect(statusTone(status)).toBe('success')
  })

  it('gives attention, failure and retired states their own tone', () => {
    expect(statusTone('needs_verification')).toBe('warning')
    expect(statusTone('PENDING')).toBe('warning')
    expect(statusTone('failed')).toBe('danger')
    expect(statusTone('Unavailable')).toBe('danger')
    expect(statusTone('DEPRECATED')).toBe('neutral')
  })

  it('keeps an unknown state neutral and labels states in sentence case', () => {
    expect(statusTone('something_new')).toBe('neutral')
    expect(statusTone(null)).toBe('neutral')
    expect(statusLabel('NEEDS_VERIFICATION')).toBe('Needs verification')
    expect(statusLabel('ACTIVE')).toBe('Active')
  })
})
