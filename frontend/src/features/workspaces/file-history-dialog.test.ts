import { describe, expect, it } from 'vitest'
import { formatBytes, formatTimestamp } from './file-history-format'

describe('formatBytes', () => {
  it('shows zero as 0 B', () => {
    expect(formatBytes(0)).toBe('0 B')
  })

  it('shows byte values', () => {
    expect(formatBytes(512)).toBe('512 B')
  })

  it('shows kilobytes with one decimal', () => {
    expect(formatBytes(2048)).toBe('2.0 KB')
  })

  it('shows megabytes with one decimal', () => {
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB')
  })
})

describe('formatTimestamp', () => {
  it('falls back for a missing value', () => {
    expect(formatTimestamp(null)).toBe('Unknown time')
  })

  it('returns the raw value when unparseable', () => {
    expect(formatTimestamp('not-a-date')).toBe('not-a-date')
  })

  it('formats an ISO timestamp', () => {
    const formatted = formatTimestamp('2026-09-19T10:00:00Z')
    expect(formatted).not.toBe('not-a-date')
    expect(formatted.length).toBeGreaterThan(0)
  })
})
