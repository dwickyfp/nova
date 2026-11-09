import { describe, expect, it } from 'vitest'
import { splitSqlStatements, statementAtOffset } from './sql-statements'

const SCRIPT = `USE pg_shop.public;

SELECT o.city, COUNT(*) AS orders
FROM orders o
WHERE o.note <> 'a;b' -- not; a split
GROUP BY o.city;
/* comment; only */
SELECT \`odd;name\` FROM t`

describe('splitSqlStatements', () => {
  it('splits on top-level semicolons only', () => {
    const texts = splitSqlStatements(SCRIPT).map((statement) => statement.text)
    expect(texts).toHaveLength(3)
    expect(texts[0]).toBe('USE pg_shop.public')
    expect(texts[1]).toMatch(/^SELECT o\.city[\s\S]*GROUP BY o\.city$/)
    expect(texts[2]).toBe('/* comment; only */\nSELECT `odd;name` FROM t')
  })

  it('drops empty and comment-only pieces', () => {
    expect(splitSqlStatements(';;  -- nothing\n; /* x */')).toEqual([])
  })

  it('keeps offsets that point at the statement in the source', () => {
    for (const statement of splitSqlStatements(SCRIPT)) {
      expect(SCRIPT.slice(statement.start, statement.end)).toBe(statement.text)
    }
  })

  it('reads doubled quotes as one escaped quote', () => {
    expect(splitSqlStatements("SELECT 'it''s; fine'; SELECT 2")).toHaveLength(2)
  })
})

describe('statementAtOffset', () => {
  const statements = splitSqlStatements(SCRIPT)

  it('finds the statement under the cursor', () => {
    expect(statementAtOffset(statements, SCRIPT.indexOf('GROUP'))?.text).toMatch(/^SELECT o\.city/)
    expect(statementAtOffset(statements, 3)?.text).toBe('USE pg_shop.public')
  })

  it('takes the statement just written when the cursor is after it', () => {
    expect(statementAtOffset(statements, SCRIPT.length)?.text).toMatch(/odd;name/)
    // On the blank line after the USE, the USE is the statement just written.
    expect(statementAtOffset(statements, SCRIPT.indexOf('\n\n') + 1)?.text).toBe('USE pg_shop.public')
  })

  it('has nothing to run in an empty file', () => {
    expect(statementAtOffset([], 0)).toBeNull()
  })
})
