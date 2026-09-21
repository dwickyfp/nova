/**
 * Client-side Ossie semantic-model draft and its YAML serializer.
 *
 * The builder edits a structured draft (datasets, fields, metrics,
 * relationships) in the UI. The backend's source of truth is the Ossie YAML, so
 * this module is the single place that turns the draft into that YAML. Keeping
 * the serializer pure means the preview and the saved document can never drift.
 *
 * Only the fields Nova's parser accepts are emitted: version 0.1.1, datasets
 * with a source and ANSI_SQL expressions, relationships with equal-length key
 * arrays, and metrics with an ANSI_SQL expression. Anything else would be
 * rejected by the backend validator, so it is not offered here.
 */

export const OSSIE_VERSION = '0.1.1'

export type DraftField = {
  /** Stable client id for React keys; never serialized. */
  id: string
  name: string
  /** SQL expression. For a plain column this equals `name`. */
  expression: string
  datatype: string
  isTimeDimension: boolean
  description: string
}

export type DraftDataset = {
  id: string
  name: string
  /** Physical source, `database.schema.table` or `database.table`. */
  source: string
  primaryKey: string
  description: string
  fields: DraftField[]
}

export type DraftMetric = {
  id: string
  name: string
  expression: string
  datatype: string
  description: string
}

export type DraftRelationship = {
  id: string
  name: string
  from: string
  to: string
  fromColumns: string
  toColumns: string
}

export type SemanticDraft = {
  name: string
  description: string
  datasets: DraftDataset[]
  metrics: DraftMetric[]
  relationships: DraftRelationship[]
}

let counter = 0

/** A unique, stable id for a new draft element. */
export function newId(prefix: string): string {
  counter += 1
  return `${prefix}-${Date.now().toString(36)}-${counter}`
}

export function emptyDraft(): SemanticDraft {
  return {
    name: '',
    description: '',
    datasets: [],
    metrics: [],
    relationships: [],
  }
}

/** Map a StarRocks column type to an Ossie `datatype`. */
export function mapDatatype(sqlType: string): string {
  const type = sqlType.toLowerCase()
  if (type.includes('datetime') || type.includes('timestamp')) return 'DateTime'
  if (type.includes('date')) return 'Date'
  if (type.includes('time')) return 'Time'
  if (type.includes('decimal') || type.includes('numeric')) return 'Decimal'
  if (type.includes('double') || type.includes('float') || type.includes('real')) return 'Float'
  if (
    type.includes('int') ||
    type.includes('bigint') ||
    type.includes('smallint') ||
    type.includes('tinyint')
  ) {
    return 'Integer'
  }
  if (type.includes('bool')) return 'Boolean'
  return 'String'
}

/** A short YAML scalar, single-quoted unless it is unambiguously plain. */
function yamlScalar(value: string): string {
  const text = value ?? ''
  if (text === '') return "''"
  // Plain scalars are safe only for a conservative character set with no
  // leading/trailing space and no indicator character anywhere. Anything else
  // (parentheses are fine to YAML, but quoting is cheaper than being wrong) is
  // single-quoted, so structure characters like `,`, `[`, `{`, `:`, `#` cannot
  // change the document's meaning.
  const safe = /^[A-Za-z0-9_][A-Za-z0-9_.\-/ ]*$/.test(text)
  const reserved = /^(true|false|null|yes|no|on|off|~)$/i.test(text)
  const numeric = /^-?\d+(\.\d+)?$/.test(text)
  if (safe && !reserved && !numeric) return text
  return `'${text.replace(/'/g, "''")}'`
}

/** A multi-line description as a YAML folded scalar on one line. */
function descriptionLine(indent: string, key: string, value: string): string {
  const text = (value ?? '').trim()
  if (!text) return ''
  return `${indent}${key}: ${yamlScalar(text)}\n`
}

/**
 * Serialize a draft to Ossie YAML (version 0.1.1).
 *
 * Deterministic: the same draft always produces byte-identical YAML, so the
 * preview does not flicker and a diff is meaningful.
 */
export function draftToOssieYaml(draft: SemanticDraft): string {
  const out: string[] = []
  out.push(`version: ${OSSIE_VERSION}`)
  out.push(`name: ${yamlScalar(draft.name || 'untitled_model')}`)
  const desc = descriptionLine('', 'description', draft.description)
  if (desc) out.push(desc.trimEnd())

  out.push('datasets:')
  if (draft.datasets.length === 0) {
    out.push('  []')
  }
  for (const dataset of draft.datasets) {
    out.push(`  - name: ${yamlScalar(dataset.name)}`)
    out.push(`    source: ${yamlScalar(dataset.source)}`)
    if (dataset.primaryKey.trim()) {
      out.push(`    primary_key: [${dataset.primaryKey.trim()}]`)
    }
    const dsDesc = descriptionLine('    ', 'description', dataset.description)
    if (dsDesc) out.push(dsDesc.trimEnd())
    if (dataset.fields.length) {
      out.push('    fields:')
      for (const field of dataset.fields) {
        out.push(`      - name: ${yamlScalar(field.name)}`)
        out.push('        expression:')
        out.push('          dialects:')
        out.push('            - dialect: ANSI_SQL')
        out.push(`              expression: ${yamlScalar(field.expression || field.name)}`)
        if (field.datatype) out.push(`        datatype: ${field.datatype}`)
        if (field.isTimeDimension) {
          out.push('        dimension:')
          out.push('          is_time: true')
        }
        const fDesc = descriptionLine('        ', 'description', field.description)
        if (fDesc) out.push(fDesc.trimEnd())
      }
    }
  }

  if (draft.relationships.length) {
    out.push('relationships:')
    for (const rel of draft.relationships) {
      out.push(`  - name: ${yamlScalar(rel.name)}`)
      out.push(`    from: ${yamlScalar(rel.from)}`)
      out.push(`    to: ${yamlScalar(rel.to)}`)
      out.push(`    from_columns: [${rel.fromColumns.trim()}]`)
      out.push(`    to_columns: [${rel.toColumns.trim()}]`)
    }
  }

  if (draft.metrics.length) {
    out.push('metrics:')
    for (const metric of draft.metrics) {
      out.push(`  - name: ${yamlScalar(metric.name)}`)
      out.push('    expression:')
      out.push('      dialects:')
      out.push('        - dialect: ANSI_SQL')
      out.push(`          expression: ${yamlScalar(metric.expression)}`)
      if (metric.datatype) out.push(`    datatype: ${metric.datatype}`)
      const mDesc = descriptionLine('    ', 'description', metric.description)
      if (mDesc) out.push(mDesc.trimEnd())
    }
  }

  return out.join('\n') + '\n'
}

/** Parse a comma/space separated column list into an array of names. */
export function columnList(value: string): string[] {
  return value
    .split(/[, ]+/)
    .map((part) => part.trim())
    .filter(Boolean)
}
