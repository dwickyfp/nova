// Formatting thresholds must distinguish adjacent DECIMAL values above 2^53.
export function compareDecimal(left: unknown, right: unknown): number | null {
  const parse = (value: unknown): [bigint, number] | null => {
    if (typeof value !== 'string' && typeof value !== 'number') return null
    const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i.exec(String(value))
    if (!match || match[2].length + (match[3]?.length ?? 0) > 1024) return null
    const exponent = Number(match[4] ?? 0)
    if (!Number.isInteger(exponent) || Math.abs(exponent) > 1024) return null
    return [BigInt(`${match[1] === '-' ? '-' : ''}${match[2]}${match[3] ?? ''}`), (match[3]?.length ?? 0) - exponent]
  }
  const a = parse(left), b = parse(right)
  if (!a || !b) return null
  const scale = Math.max(a[1], b[1])
  const av = a[0] * 10n ** BigInt(scale - a[1]), bv = b[0] * 10n ** BigInt(scale - b[1])
  return av < bv ? -1 : av > bv ? 1 : 0
}
