import { afterEach, describe, expect, it } from 'vitest'
import { readToken } from './read-token'

afterEach(() => {
  document.documentElement.style.removeProperty('--probe-token')
})

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
})
