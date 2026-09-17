/**
 * @vitest-environment node
 */
import { describe, expect, it } from 'vitest'
import { isTokenFallbackArgument } from '../../eslint.design-system.js'

/**
 * A2's exemption is a syntactic predicate, so it is tested against literal AST
 * shapes rather than by running ESLint (which cannot be bundled into the test
 * runner). Full-rule behaviour is covered by `pnpm lint` against real files.
 */
type Node = {
  type?: string
  value?: unknown
  name?: string
  parent?: Node | null
  callee?: Node
  arguments?: Node[]
}

const literal = (value: string): Node => ({ type: 'Literal', value })
const identifier = (name: string): Node => ({ type: 'Identifier', name })

const shape = (calleeName: string, ...args: Node[]) => {
  const call: Node = {
    type: 'CallExpression',
    callee: identifier(calleeName),
    arguments: args,
    parent: null,
  }
  const hex: Node = { type: 'Literal', value: '#d04738', parent: call }
  return isTokenFallbackArgument(hex)
}

describe('isTokenFallbackArgument', () => {
  it('allows readToken(name, hex)', () => {
    expect(shape('readToken', literal('--chart-1'), literal('#f05a47'))).toBe(
      true
    )
  })

  it('allows a hex in a later argument of a token reader', () => {
    expect(
      shape('readColorToken', literal('--x'), literal('y'), literal('#d04738'))
    ).toBe(true)
  })

  it('rejects a hex in a plain helper call', () => {
    expect(shape('cn', literal('bg-emerald-600'), literal('#d04738'))).toBe(
      false
    )
  })

  it('rejects a hex passed to a non-token-reader function', () => {
    expect(shape('makeColor', literal('--chart-1'), literal('#f05a47'))).toBe(
      false
    )
  })

  it('rejects a token reader whose first argument is an identifier', () => {
    expect(shape('readToken', identifier('someVar'), literal('#d04738'))).toBe(
      false
    )
  })

  it('rejects a token reader whose first argument is not a custom property', () => {
    expect(shape('readToken', literal('chart-1'), literal('#f05a47'))).toBe(
      false
    )
  })

  it('rejects a token reader with no first argument', () => {
    expect(shape('readToken')).toBe(false)
  })

  it('rejects a literal with no call parent', () => {
    expect(
      isTokenFallbackArgument({
        type: 'Literal',
        value: '#d04738',
        parent: null,
      })
    ).toBe(false)
  })

  it('rejects a hex outside any call', () => {
    expect(
      isTokenFallbackArgument({
        type: 'Literal',
        value: '#d04738',
        parent: { type: 'VariableDeclarator' },
      })
    ).toBe(false)
  })
})
