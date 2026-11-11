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

  it("keeps a task's BEGIN ... END block as one statement", () => {
    const script = `CREATE TASK etl.public.refresh AS BEGIN
  UPDATE stock SET level = CASE WHEN qty > 0 THEN 'ok' ELSE 'empty' END WHERE id > 0;
  INSERT INTO stock_log SELECT * FROM stock;
END;
EXECUTE TASK etl.public.refresh`
    const texts = splitSqlStatements(script).map((statement) => statement.text)
    expect(texts).toHaveLength(2)
    expect(texts[0]).toMatch(/^CREATE TASK[\s\S]*INSERT INTO stock_log SELECT \* FROM stock;\nEND$/)
    expect(texts[1]).toBe('EXECUTE TASK etl.public.refresh')
  })

  it('keeps a $$ body as one statement', () => {
    const script = 'CREATE TASK t LANGUAGE PYTHON AS $$\nx = 1; y = 2\n$$;\nSELECT a$$b; SELECT 3'
    const texts = splitSqlStatements(script).map((statement) => statement.text)
    expect(texts).toEqual([
      'CREATE TASK t LANGUAGE PYTHON AS $$\nx = 1; y = 2\n$$',
      'SELECT a$$b',
      'SELECT 3',
    ])
  })

  it('still splits a transaction BEGIN and an alias named begin', () => {
    expect(splitSqlStatements('BEGIN; INSERT INTO t VALUES (1); COMMIT')).toHaveLength(3)
    expect(splitSqlStatements('SELECT 1 AS begin; SELECT 2')).toHaveLength(2)
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
