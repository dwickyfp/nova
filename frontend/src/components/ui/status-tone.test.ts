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

describe('engine job and query states', () => {
  it('maps finished, in-flight and stopped engine states', () => {
    for (const status of ['SUCCESS', 'FINISHED', 'done', 'OK'])
      expect(statusTone(status)).toBe('success')
    for (const status of ['LOADING', 'PENDING', 'QUEUEING', 'IN_PROGRESS'])
      expect(statusTone(status)).toBe('warning')
    for (const status of ['ERROR', 'FAILED', 'CANCEL', 'CANCELLED', 'canceled', 'KILL', 'KILLED'])
      expect(statusTone(status)).toBe('danger')
    expect(statusTone('RUNNING')).toBe('info')
  })
})
