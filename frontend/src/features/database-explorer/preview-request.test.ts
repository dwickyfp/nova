import { describe, expect, it } from 'vitest'
import { formatElapsed, previewRequest } from './preview-request'

describe('previewRequest', () => {
  it('names a source catalog relation in full and sends no database context', () => {
    expect(previewRequest('sf_sales', 'SALES', 'ORDERS')).toEqual({
      sql: 'SELECT * FROM `sf_sales`.`SALES`.`ORDERS` LIMIT 10',
      max_rows: 10,
    })
  })

  it('keeps Nova tables in their database context', () => {
    expect(previewRequest('default_catalog', 'analytics', 'orders')).toEqual({
      sql: 'SELECT * FROM `analytics`.`orders` LIMIT 10',
      database: 'analytics',
      max_rows: 10,
    })
  })

  it('escapes backticks in identifiers', () => {
    expect(previewRequest('pg', 'public', 'odd`name').sql).toBe(
      'SELECT * FROM `pg`.`public`.`odd``name` LIMIT 10',
    )
  })
})

describe('formatElapsed', () => {
  it('reads at a glance at every scale', () => {
    expect(formatElapsed(434.8)).toBe('435 ms')
    expect(formatElapsed(5_033)).toBe('5.0 s')
    expect(formatElapsed(162_094.64)).toBe('2 min 42 s')
    expect(formatElapsed(120_000)).toBe('2 min')
  })
})
