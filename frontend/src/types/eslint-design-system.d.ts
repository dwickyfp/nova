/**
 * The design-system gate lives in plain JS because ESLint loads it directly.
 * This declares its exports so tests can import it without `any`.
 */
declare module '*/eslint.design-system.js' {
  export const TOKEN_READER_NAME: RegExp

  export function isTokenFallbackArgument(node: {
    type?: string
    value?: unknown
    name?: string
    parent?: unknown
    callee?: unknown
    arguments?: unknown[]
  }): boolean

  export const novaPlugin: { rules: Record<string, unknown> }
}
