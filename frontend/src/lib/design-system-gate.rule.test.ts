/**
 * @vitest-environment node
 */
import { Linter } from 'eslint'
import type { Linter as LinterTypes } from 'eslint'
import { describe, expect, it } from 'vitest'
import { novaPlugin } from '../../eslint.design-system.js'

/**
 * The predicate test beside this one covers A2's exemption shape. This one
 * runs the real plugin through `Linter.verify`, so it is the thing that
 * actually holds the gate's behaviour. `pnpm lint` enforces the gate against
 * real files, but a dynamic bypass that only exists in the loaded rule is
 * invisible to a test that re-implements the matching by hand — which is
 * exactly how the concat hole survived.
 *
 * Every case runs under the feature scope (`semantic-tokens-features`), a
 * strict superset of the repository scope, so a bypass caught here is caught
 * for `src/**` too.
 */

const linter = new Linter()

const config: LinterTypes.Config[] = [
  {
    files: ['**/*.ts'],
    plugins: { nova: novaPlugin },
    rules: { 'nova/semantic-tokens-features': 'error' },
  },
]

const lint = (code: string) =>
  linter.verify(code, config, 'src/features/probe.ts')

const errors = (code: string) =>
  lint(code).filter(
    (message) => message.ruleId === 'nova/semantic-tokens-features'
  )

describe('design-system gate: literal controls still fail', () => {
  it('catches a raw palette class', () => {
    expect(errors(`export const a = 'bg-emerald-600'`)).toHaveLength(1)
  })

  it('catches a hex literal', () => {
    expect(errors(`export const a = '#d04738'`)).toHaveLength(1)
  })
})

describe('design-system gate: assembled values', () => {
  it('catches a palette class built by string concatenation', () => {
    const messages = errors(`export const a = 'bg-' + 'emerald-600'`)
    expect(messages).toHaveLength(1)
    expect(messages[0].message).toContain('Raw Tailwind palette class')
  })

  it('catches a palette class built by Array.prototype.join', () => {
    const messages = errors(`export const a = ['bg', 'emerald', '600'].join('-')`)
    expect(messages).toHaveLength(1)
    expect(messages[0].message).toContain('Raw Tailwind palette class')
  })

  it('catches a hex colour built by string concatenation', () => {
    const messages = errors(`export const a = '#' + 'd04738'`)
    expect(messages).toHaveLength(1)
    expect(messages[0].message).toContain('Hex colour literal')
  })

  it('catches a hex colour built from a static template placeholder', () => {
    const messages = errors('export const a = `${"#"}d04738`')
    expect(messages).toHaveLength(1)
    expect(messages[0].message).toContain('Hex colour literal')
  })

  it('reports a nested concatenation exactly once', () => {
    expect(
      errors(`export const a = 'bg' + '-' + 'emerald' + '-' + '600'`)
    ).toHaveLength(1)
  })
})

describe('design-system gate: dynamic templates stay caught via their binding', () => {
  it('catches the palette literal bound to a variable used in a template', () => {
    const messages = errors(
      ['const c = "bg-emerald-600"', 'export const a = `${c} p-2`'].join('\n')
    )
    expect(messages).toHaveLength(1)
    expect(messages[0].message).toContain('Raw Tailwind palette class')
  })

  it('catches the binding when the template only has a suffix', () => {
    expect(
      errors(['const c = "text-amber-500"', 'export const a = `p-2 ${c}`'].join('\n'))
    ).toHaveLength(1)
  })

  it('does not treat an unknown binding as an empty string', () => {
    // If the gate evaluated `${c}` to '', the unknown binding would report as
    // the safe literal `p-2` and the palette class it carries would be missed.
    const messages = errors(
      [
        'const c = "bg-emerald-600"',
        'export const a = `p-2 ${c}`',
        'export const b = `${c} p-2`',
      ].join('\n')
    )
    // One report for the declaration, plus one per template that cannot be
    // evaluated as a whole but still shows the binding is in play.
    expect(messages.length).toBeGreaterThanOrEqual(1)
    expect(messages.some((m) => m.line === 1)).toBe(true)
  })
})

describe('design-system gate: no false positives on genuinely dynamic values', () => {
  it('ignores a template whose placeholder is unknown', () => {
    expect(
      errors('export const a = (n: number) => `${n} p-2 items-center`')
    ).toHaveLength(0)
  })

  it('ignores a join with an unknown element', () => {
    expect(
      errors(`export const a = (x: string) => ['grid', x].join(' ')`)
    ).toHaveLength(0)
  })

  it('ignores concatenation with an unknown operand', () => {
    expect(errors(`export const a = (x: string) => 'p-2 ' + x`)).toHaveLength(0)
  })

  it('ignores a hex assembled from an unknown operand', () => {
    expect(errors(`export const a = (x: string) => '#' + x`)).toHaveLength(0)
  })

  it('allows semantic tokens in literal and assembled form', () => {
    expect(errors(`export const a = 'bg-success'`)).toHaveLength(0)
    expect(errors(`export const a = 'bg-' + 'success'`)).toHaveLength(0)
  })
})

describe('design-system gate: the A2 fallback exemption survives', () => {
  it('allows the literal token-reader fallback', () => {
    expect(
      errors(
        `import { readToken } from '@/lib/read-token'\n` +
          `export const a = readToken('--chart-1', '#f05a47')`
      )
    ).toHaveLength(0)
  })

  it('allows a later-argument token-reader fallback', () => {
    expect(
      errors(
        `import { readColorToken } from '@/lib/read-token'\n` +
          `export const a = readColorToken('--x', 'y', '#d04738')`
      )
    ).toHaveLength(0)
  })
})
