const BI_ROOT = '/bi/'

/** Keep the return target inside Superset; anything else opens its home. */
export function safeBiTarget(next: string | undefined): string {
  if (!next || !next.startsWith(BI_ROOT) || next.startsWith('//')) return BI_ROOT
  return next
}
