import { afterEach, describe, expect, it } from 'vitest'
import './index.css'

/**
 * WCAG AA for the theme tokens, measured on the real stylesheet in both modes
 * so a palette edit in theme.css cannot quietly drop below the bar (DESIGN.md B4).
 */

const TEXT: Array<[foreground: string, background: string]> = [
  ['--foreground', '--background'],
  ['--primary', '--background'],
  ['--primary-foreground', '--primary'],
  ['--brand', '--background'],
  ['--brand', '--card'],
  ['--brand', '--accent'],
  ['--brand-foreground', '--brand'],
  ['--accent-foreground', '--accent'],
  ['--muted-foreground', '--background'],
  ['--muted-foreground', '--accent'],
]

/** Non-text marks: focus ring, chart series and the highlighted mark (3:1). */
const NON_TEXT: Array<[mark: string, background: string]> = [
  ['--ring', '--background'],
  ['--ring', '--card'],
  ['--viz-highlight', '--card'],
  ['--viz-mark', '--card'],
  ...Array.from({ length: 9 }, (_, index) => [`--chart-${index + 1}`, '--card'] as [string, string]),
]

function rgb(color: string): [number, number, number] {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const context = canvas.getContext('2d')!
  context.fillStyle = color
  context.fillRect(0, 0, 1, 1)
  const [r, g, b] = context.getImageData(0, 0, 1, 1).data
  return [r, g, b]
}

function luminance(color: string): number {
  const [r, g, b] = rgb(color).map(value => {
    const channel = value / 255
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function ratio(foreground: string, background: string): number {
  const a = luminance(foreground)
  const b = luminance(background)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

function token(name: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  expect(value, `${name} is defined`).not.toBe('')
  return value
}

/** OKLab coordinates, where Euclidean distance tracks perceived difference. */
function oklab(color: string): [number, number, number] {
  const [r, g, b] = rgb(color).map(value => {
    const channel = value / 255
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ]
}

function distance(a: string, b: string): number {
  const [x, y] = [oklab(a), oklab(b)]
  return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2])
}

describe.each(['light', 'dark'] as const)('theme contrast (%s)', mode => {
  afterEach(() => document.documentElement.classList.remove('dark'))

  const apply = () => document.documentElement.classList.toggle('dark', mode === 'dark')

  it.each(TEXT)('%s on %s is at least 4.5:1', (foreground, background) => {
    apply()
    expect(ratio(token(foreground), token(background))).toBeGreaterThanOrEqual(4.5)
  })

  it.each(NON_TEXT)('%s on %s is at least 3:1', (mark, background) => {
    apply()
    expect(ratio(token(mark), token(background))).toBeGreaterThanOrEqual(3)
  })

  it('keeps the primary action apart from destructive and the interactive accent', () => {
    apply()
    // Graphite vs red vs steel blue: clearly different colours, not two shades of one hue.
    expect(distance(token('--primary'), token('--destructive'))).toBeGreaterThan(0.2)
    expect(distance(token('--primary'), token('--brand'))).toBeGreaterThan(0.15)
    expect(distance(token('--brand'), token('--destructive'))).toBeGreaterThan(0.2)
  })

  it('never puts two look-alike series next to each other', () => {
    apply()
    const series = Array.from({ length: 9 }, (_, index) => token(`--chart-${index + 1}`))
    for (let index = 1; index < series.length; index += 1) {
      expect(distance(series[index - 1], series[index]), `--chart-${index} vs --chart-${index + 1}`).toBeGreaterThan(0.08)
    }
    // Non-adjacent slots may be closer (sky and steel blue), but never the same.
    for (let a = 0; a < series.length; a += 1) {
      for (let b = a + 1; b < series.length; b += 1) {
        expect(distance(series[a], series[b]), `--chart-${a + 1} vs --chart-${b + 1}`).toBeGreaterThan(0.06)
      }
    }
  })
})
