import { afterEach, describe, expect, it } from 'vitest'
import { readModeToken, readToken } from './read-token'

afterEach(() => {
  document.documentElement.style.removeProperty('--probe-token')
  document.getElementById('read-mode-token-probe')?.remove()
})

/**
 * Scopes a probe token to the two mode classes the way theme.css does, so the
 * per-mode reader can be checked without depending on the real palette.
 */
function defineModeProbeToken(light: string, dark: string) {
  const style = document.createElement('style')
  style.id = 'read-mode-token-probe'
  style.textContent = `
    .light { --probe-token: ${light}; }
    .dark { --probe-token: ${dark}; }
  `
  document.head.appendChild(style)
}

describe('readToken', () => {
  it('returns the fallback for a token that is not defined', () => {
    expect(readToken('--probe-token', '#f05a47')).toBe('#f05a47')
  })

  it('returns the resolved token value when the token is defined', () => {
    document.documentElement.style.setProperty('--probe-token', '#abcdef')
    expect(readToken('--probe-token', '#000000')).toBe('#abcdef')
  })

  it('keeps the fallback position, which is the shape the gate exempts', () => {
    // A hex is only legitimate as the second argument of a read*Token call.
    expect(readToken('--probe-token', '#abcdef')).toBe('#abcdef')
  })

  it('normalises an oklch token to hex for consumers that reject it', () => {
    document.documentElement.style.setProperty(
      '--probe-token',
      'oklch(0.129 0.042 264.695)'
    )
    expect(readToken('--probe-token', '#000000')).toMatch(/^#[0-9a-fA-F]{6}$/)
  })
})

describe('readModeToken', () => {
  it('reads the requested mode instead of the live document theme', () => {
    defineModeProbeToken('#111111', '#222222')
    document.documentElement.classList.remove('light', 'dark')
    document.documentElement.classList.add('light')

    // The document is light; asking for dark must still return the dark token.
    expect(readModeToken('--probe-token', 'dark', '#000000')).toBe('#222222')
    expect(readModeToken('--probe-token', 'light', '#000000')).toBe('#111111')
  })

  it('does not depend on any mode class being present on the document', () => {
    defineModeProbeToken('#333333', '#444444')
    document.documentElement.classList.remove('light', 'dark')

    expect(readModeToken('--probe-token', 'light', '#000000')).toBe('#333333')
    expect(readModeToken('--probe-token', 'dark', '#000000')).toBe('#444444')
  })

  it('returns the fallback when the token is absent in both modes', () => {
    expect(readModeToken('--probe-token', 'light', '#f05a47')).toBe('#f05a47')
  })

  it('leaves no probe element behind in the document', () => {
    defineModeProbeToken('#555555', '#666666')
    readModeToken('--probe-token', 'dark', '#000000')
    expect(document.documentElement.querySelectorAll('div.light, div.dark')).toHaveLength(0)
  })
})
