/** One statement of a script, with its character offsets in the source. */
export type SqlStatement = { text: string; start: number; end: number }

/**
 * Splits a script on top-level `;`, ignoring semicolons inside quotes,
 * backticks and comments. Statements that are only whitespace or comments are
 * dropped. The server splits the same way when it runs a whole script; this is
 * only for choosing which statements to send.
 */
export function splitSqlStatements(source: string): SqlStatement[] {
  const statements: SqlStatement[] = []
  let start = 0
  let index = 0
  const push = (end: number) => {
    const raw = source.slice(start, end)
    if (stripComments(raw).trim()) {
      const leading = raw.length - raw.trimStart().length
      const text = raw.trim()
      statements.push({ text, start: start + leading, end: start + leading + text.length })
    }
  }
  while (index < source.length) {
    const char = source[index]
    const next = source[index + 1]
    if (char === "'" || char === '"' || char === '`') {
      index = skipQuoted(source, index, char)
    } else if (char === '-' && next === '-') {
      index = lineEnd(source, index)
    } else if (char === '#') {
      index = lineEnd(source, index)
    } else if (char === '/' && next === '*') {
      const close = source.indexOf('*/', index + 2)
      index = close === -1 ? source.length : close + 2
    } else if (char === ';') {
      push(index)
      start = index + 1
      index += 1
    } else {
      index += 1
    }
  }
  push(source.length)
  return statements
}

/**
 * The statement the cursor is in. Between statements, or after the last one,
 * it is the statement just before the cursor, which is the one just written.
 */
export function statementAtOffset(
  statements: SqlStatement[],
  offset: number,
): SqlStatement | null {
  let before: SqlStatement | null = null
  for (const statement of statements) {
    if (offset >= statement.start && offset <= statement.end) return statement
    if (statement.end <= offset) before = statement
  }
  return before ?? statements[0] ?? null
}

function skipQuoted(source: string, from: number, quote: string): number {
  let index = from + 1
  while (index < source.length) {
    if (source[index] === '\\' && quote !== '`') {
      index += 2
    } else if (source[index] === quote) {
      // A doubled quote is an escaped quote inside the literal.
      if (source[index + 1] === quote) index += 2
      else return index + 1
    } else {
      index += 1
    }
  }
  return source.length
}

function lineEnd(source: string, from: number): number {
  const newline = source.indexOf('\n', from)
  return newline === -1 ? source.length : newline + 1
}

function stripComments(sql: string): string {
  return sql.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(--|#)[^\n]*/g, '')
}
