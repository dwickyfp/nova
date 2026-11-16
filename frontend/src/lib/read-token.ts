/**
 * Recharts takes colours as concrete values, not Tailwind classes, so a chart
 * needs the resolved token value at runtime. Reading it off the document keeps
 * the chart on the same palette as the rest of the UI, including dark mode.
 *
 * The fallback is required: getComputedStyle does not exist during SSR. It is
 * the only sanctioned place for a hex literal outside theme.css, enforced by
 * the design-system gate in DESIGN.md section 3 A2.
 */

type Rgb = [number, number, number]

const clamp01 = (n: number) => Math.min(1, Math.max(0, n))

/** sRGB transfer function (linear-light → gamma-encoded). */
function encodeSrgb(linear: number): number {
  const c = clamp01(linear)
  return c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055
}

/** OKLCH → OKLab → linear sRGB → gamma sRGB. */
function oklchToRgb(l: number, c: number, hDeg: number): Rgb {
  const h = (hDeg * Math.PI) / 180
  const a = c * Math.cos(h)
  const b = c * Math.sin(h)

  const l_ = l + 0.3963377774 * a + 0.2158037573 * b
  const m_ = l - 0.1055613458 * a - 0.0638541728 * b
  const s_ = l - 0.0894841775 * a - 1.291485548 * b

  const l3 = l_ ** 3
  const m3 = m_ ** 3
  const s3 = s_ ** 3

  const r = 4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3
  const g = -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3
  const bl = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.707614701 * s3

  return [encodeSrgb(r), encodeSrgb(g), encodeSrgb(bl)]
}

function rgbToHex([r, g, b]: Rgb): string {
  return `#${[r, g, b]
    .map((v) => Math.round(v * 255).toString(16).padStart(2, '0'))
    .join('')}`
}

function toHex(value: string): string {
  if (value.startsWith('#')) return value
  const match = value.match(
    /^oklch\(\s*([\d.]+%?)\s+([\d.]+)\s+([\d.]+)(?:deg)?\s*\)$/i
  )
  if (!match) return value
  const lRaw = match[1].endsWith('%')
    ? Number.parseFloat(match[1]) / 100
    : Number.parseFloat(match[1])
  return rgbToHex(
    oklchToRgb(lRaw, Number.parseFloat(match[2]), Number.parseFloat(match[3]))
  )
}

export function readToken(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim()
  return toHex(value || fallback)
}

/**
 * Reads a token for an explicit mode instead of the live document theme.
 *
 * Monaco themes are defined for both `nova-light` and `nova-dark` in a single
 * call, so reading the current document class is wrong: whichever mode the
 * document happens to be in would be baked into *both* definitions, and the
 * first call (which can run before the theme provider swaps the class) would
 * poison the pair. The probe element carries the target class and is measured
 * off-document layout, so it never paints or shifts anything.
 */
export function readModeToken(
  name: string,
  mode: 'light' | 'dark',
  fallback: string
): string {
  if (typeof document === 'undefined') return fallback
  const probe = document.createElement('div')
  probe.className = mode
  probe.style.position = 'absolute'
  probe.style.visibility = 'hidden'
  probe.style.pointerEvents = 'none'
  document.documentElement.appendChild(probe)
  const value = getComputedStyle(probe).getPropertyValue(name).trim()
  probe.remove()
  return toHex(value || fallback)
}

function rootDeclarations(): Map<string, string> {
  const values = new Map<string, string>()
  const visit = (rules: CSSRuleList) => {
    for (const rule of Array.from(rules)) {
      if (rule instanceof CSSStyleRule) {
        if (rule.selectorText.split(',').some(selector => selector.trim() === ':root')) {
          // Declarations after a nested rule live in nested-declaration children.
          const blocks = [rule.style, ...Array.from(rule.cssRules ?? [])
            .filter(child => !('selectorText' in child) && 'style' in child)
            .map(child => (child as CSSRule & { style: CSSStyleDeclaration }).style)]
          for (const style of blocks) {
            for (let i = 0; i < style.length; i++) {
              const property = style.item(i)
              if (property.startsWith('--')) values.set(property, style.getPropertyValue(property).trim())
            }
          }
        }
      } else if ('cssRules' in rule && (rule as CSSGroupingRule).cssRules) {
        visit((rule as CSSGroupingRule).cssRules)
      }
    }
  }
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      visit(sheet.cssRules)
    } catch {
      // A cross-origin sheet cannot be read; theme tokens are never in one.
    }
  }
  return values
}

/**
 * A token's light-theme value even while the page is dark: read from the
 * `:root` declarations themselves, since every element inherits the dark
 * values from `<html class="dark">`. Used to draw printouts on white paper.
 */
export function readLightToken(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback
  const declared = rootDeclarations()
  let value = declared.get(name) ?? ''
  for (let depth = 0; depth < 8; depth++) {
    const reference = /^var\((--[\w-]+)(?:,\s*(.+))?\)$/.exec(value)
    if (!reference) break
    value = declared.get(reference[1]) ?? reference[2] ?? ''
  }
  return toHex(value || fallback)
}
