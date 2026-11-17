// Types for the license gate's exports, used by its rule test.
export const ALLOWED: Set<string>
export const REVIEWED: Record<string, [string[], string]>
export const COMMERCIAL: RegExp
export const LICENSE_KEY_API: RegExp
export function accepted(expression: string, name: string): boolean
export function declared(pkg: { license?: unknown; licenses?: unknown }): string | null
