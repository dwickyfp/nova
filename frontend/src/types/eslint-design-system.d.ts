/**
 * The design-system gate lives in plain JS because ESLint loads it directly.
 * This declares its exports so tests can import it without `any`.
 */
declare module '*/eslint.design-system.js' {
  import type { Plugin } from 'eslint'

  export const TOKEN_READER_NAME: RegExp

  export function isTokenFallbackArgument(node: {
    type?: string
    value?: unknown
    name?: string
    parent?: unknown
    callee?: unknown
    arguments?: unknown[]
  }): boolean

  export const UNKNOWN: null

  export function staticString(node: unknown): string | null

  export function joinedSeparator(node: unknown): string | null

  export function assembledValues(node: unknown): string[]

  export const novaPlugin: Plugin
}
