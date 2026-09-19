import { describe, expect, it } from 'vitest'
import {
  applyHunks,
  buildRewriteHunks,
  diffLines,
  extractSqlCodeBlock,
  formatAttachmentsForPrompt,
  type AttachedQuery,
  type RewriteHunk,
} from './query-attach'

const baseAttachment: AttachedQuery = {
  id: 'att-1',
  sql: 'SELECT * FROM t',
  tabId: 'tab-1',
  fileName: 'query.sql',
  database: 'db',
  schema: 'main',
  role: 'ACCOUNTADMIN',
  startLine: 1,
  endLine: 1,
  createdAt: 0,
}

describe('diffLines', () => {
  it('marks identical documents as all equal', () => {
    expect(diffLines(['a', 'b'], ['a', 'b'])).toEqual([
      { type: 'equal', line: 'a' },
      { type: 'equal', line: 'b' },
    ])
  })

  it('ignores trailing whitespace differences', () => {
    expect(diffLines(['a  '], ['a'])).toEqual([{ type: 'equal', line: 'a  ' }])
  })

  it('emits a delete then insert for a changed line', () => {
    expect(diffLines(['a', 'b'], ['a', 'c'])).toEqual([
      { type: 'equal', line: 'a' },
      { type: 'delete', line: 'b' },
      { type: 'insert', line: 'c' },
    ])
  })
})

describe('buildRewriteHunks', () => {
  it('returns no hunks for an identical document', () => {
    expect(buildRewriteHunks('SELECT 1\nFROM t', 'SELECT 1\nFROM t')).toEqual([])
  })

  it('collapses a changed line into one replace hunk', () => {
    const hunks = buildRewriteHunks('SELECT a\nFROM t', 'SELECT b\nFROM t')
    expect(hunks).toEqual([{ kind: 'replace', startLine: 1, endLine: 1, lines: ['SELECT b'] }])
  })

  it('reports only the changed lines for a partial rewrite', () => {
    const before = 'SELECT a,\n       b,\n       c\nFROM t'
    const after = 'SELECT a,\n       b,\n       c\nFROM t\nWHERE a > 0'
    const hunks = buildRewriteHunks(before, after)
    expect(hunks).toEqual([{ kind: 'insert', startLine: 4, endLine: 4, lines: ['WHERE a > 0'] }])
  })
  it('reports a deletion with no proposed lines', () => {
    const hunks = buildRewriteHunks('SELECT 1\n-- comment\nFROM t', 'SELECT 1\nFROM t')
    expect(hunks).toEqual([{ kind: 'delete', startLine: 2, endLine: 2, lines: [] }])
  })

  it('reports a multi-line replacement as one hunk', () => {
    const hunks = buildRewriteHunks('SELECT a\nFROM t', 'SELECT a, b, c\nFROM big_table')
    expect(hunks).toEqual([
      { kind: 'replace', startLine: 1, endLine: 2, lines: ['SELECT a, b, c', 'FROM big_table'] },
    ])
  })
})

describe('applyHunks', () => {
  it('round-trips the proposed document from its hunks', () => {
    const before = 'SELECT a,\n       b,\n       c\nFROM t'
    const after = 'SELECT a,\n       b,\n       c\nFROM t\nWHERE a > 0'
    expect(applyHunks(before, buildRewriteHunks(before, after))).toBe(after)
  })

  it('applies multiple hunks without shifting earlier lines', () => {
    const before = 'SELECT a\nFROM t\nWHERE a > 0'
    const after = 'SELECT b\nFROM t\nWHERE b > 0'
    expect(applyHunks(before, buildRewriteHunks(before, after))).toBe(after)
  })

  it('deletes a line', () => {
    const before = 'SELECT 1\n-- comment\nFROM t'
    const after = 'SELECT 1\nFROM t'
    expect(applyHunks(before, buildRewriteHunks(before, after))).toBe(after)
  })

  it('applies a subset of hunks', () => {
    const before = 'SELECT a\nFROM t\nWHERE a > 0'
    const hunks = buildRewriteHunks(before, 'SELECT b\nFROM t\nWHERE b > 0')
    const onlyFirst: RewriteHunk[] = [hunks[0]]
    expect(applyHunks(before, onlyFirst)).toBe('SELECT b\nFROM t\nWHERE a > 0')
  })
})

describe('extractSqlCodeBlock', () => {
  it('returns null when there is no fenced block', () => {
    expect(extractSqlCodeBlock('Here is the explanation.')).toBeNull()
  })

  it('extracts a single fenced sql block', () => {
    expect(extractSqlCodeBlock('Try this:\n```sql\nSELECT 1\n```')).toBe('SELECT 1')
  })

  it('prefers the last complete block', () => {
    const markdown = '```sql\nSELECT 1\n```\nBetter:\n```sql\nSELECT 2\n```'
    expect(extractSqlCodeBlock(markdown)).toBe('SELECT 2')
  })

  it('ignores an unterminated trailing block', () => {
    expect(extractSqlCodeBlock('```sql\nSELECT 1\n```\n```sql\nSELECT 2')).toBe('SELECT 1')
  })

  it('accepts the mysql language tag', () => {
    expect(extractSqlCodeBlock('```mysql\nSELECT 1\n```')).toBe('SELECT 1')
  })
})

describe('formatAttachmentsForPrompt', () => {
  it('returns an empty string with no attachments', () => {
    expect(formatAttachmentsForPrompt([])).toBe('')
  })

  it('includes the SQL and worksheet metadata', () => {
    const prompt = formatAttachmentsForPrompt([baseAttachment])
    expect(prompt).toContain('file: query.sql')
    expect(prompt).toContain('database: db')
    expect(prompt).toContain('schema: main')
    expect(prompt).toContain('role: ACCOUNTADMIN')
    expect(prompt).toContain('```sql\nSELECT * FROM t\n```')
    expect(prompt.endsWith('---\n\n')).toBe(true)
  })

  it('numbers multiple attachments', () => {
    const prompt = formatAttachmentsForPrompt([
      baseAttachment,
      { ...baseAttachment, id: 'att-2', sql: 'SELECT 2' },
    ])
    expect(prompt).toContain('Attached query 1')
    expect(prompt).toContain('Attached query 2')
  })
})
