export const PREVIEW_LIMIT = 10

function quoteIdentifier(identifier: string) {
  return `\`${identifier.replace(/`/g, '``')}\``
}

/**
 * The preview statement. A relation in another catalog is named in full,
 * `catalog.schema.relation`, and runs without a database context: Nova's
 * session database lives in its own catalog.
 */
export function previewRequest(catalog: string, database: string, table: string) {
  const relation = `${quoteIdentifier(database)}.${quoteIdentifier(table)}`
  if (catalog && catalog !== 'default_catalog') {
    return {
      sql: `SELECT * FROM ${quoteIdentifier(catalog)}.${relation} LIMIT ${PREVIEW_LIMIT}`,
      max_rows: PREVIEW_LIMIT,
    }
  }
  return {
    sql: `SELECT * FROM ${relation} LIMIT ${PREVIEW_LIMIT}`,
    database,
    max_rows: PREVIEW_LIMIT,
  }
}

/** A query duration people read at a glance: `420 ms`, `5.2 s`, `2 min 42 s`. */
export function formatElapsed(ms: number) {
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return seconds ? `${minutes} min ${seconds} s` : `${minutes} min`
}
