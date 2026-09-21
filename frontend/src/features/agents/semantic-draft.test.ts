import { describe, expect, it } from 'vitest'
import {
  draftToOssieYaml,
  emptyDraft,
  mapDatatype,
  newId,
  type SemanticDraft,
} from './semantic-draft'

/**
 * The builder's contract is that the YAML it produces is valid Ossie the backend
 * accepts. These tests pin the serializer's shape and its edge cases (quoting,
 * empty sections, computed fields).
 */

function draft(overrides: Partial<SemanticDraft> = {}): SemanticDraft {
  return {
    ...emptyDraft(),
    name: 'sales_analytics',
    description: 'Sales and customer analytics',
    ...overrides,
  }
}

describe('mapDatatype', () => {
  it('maps StarRocks types to Ossie datatypes', () => {
    expect(mapDatatype('bigint')).toBe('Integer')
    expect(mapDatatype('decimal(14,2)')).toBe('Decimal')
    expect(mapDatatype('datetime')).toBe('DateTime')
    expect(mapDatatype('date')).toBe('Date')
    expect(mapDatatype('varchar(255)')).toBe('String')
    expect(mapDatatype('double')).toBe('Float')
    expect(mapDatatype('boolean')).toBe('Boolean')
  })
})

describe('draftToOssieYaml', () => {
  it('emits the version and name first', () => {
    const yaml = draftToOssieYaml(draft())
    expect(yaml.split('\n')[0]).toBe('version: 0.1.1')
    expect(yaml.split('\n')[1]).toBe('name: sales_analytics')
  })

  it('emits a dataset with a source and fields', () => {
    const yaml = draftToOssieYaml(
      draft({
        datasets: [
          {
            id: newId('ds'),
            name: 'orders',
            source: 'NOVA_DEMO.orders',
            primaryKey: 'order_id',
            description: 'One row per order',
            fields: [
              {
                id: newId('f'),
                name: 'order_date',
                expression: 'order_date',
                datatype: 'DateTime',
                isTimeDimension: true,
                description: 'When the order was placed',
              },
            ],
          },
        ],
      })
    )
    expect(yaml).toContain('datasets:')
    expect(yaml).toContain('- name: orders')
    expect(yaml).toContain('source: NOVA_DEMO.orders')
    expect(yaml).toContain('primary_key: [order_id]')
    expect(yaml).toContain('dialect: ANSI_SQL')
    expect(yaml).toContain('is_time: true')
    expect(yaml).toContain('datatype: DateTime')
  })

  it('quotes a varcharlike value that could be misread', () => {
    const yaml = draftToOssieYaml(
      draft({
        metrics: [
          {
            id: newId('m'),
            name: 'total_revenue',
            expression: "SUM(orders.total_amount)",
            datatype: 'Decimal',
            description: '',
          },
        ],
      })
    )
    // The expression has parentheses and a dot; it must be quoted so YAML does
    // not treat it as structure.
    expect(yaml).toContain("expression: 'SUM(orders.total_amount)'")
  })

  it('quotes a description containing a colon', () => {
    const yaml = draftToOssieYaml(
      draft({
        datasets: [
          {
            id: newId('ds'),
            name: 'orders',
            source: 'db.orders',
            primaryKey: '',
            description: 'Orders: one row per order',
            fields: [],
          },
        ],
      })
    )
    expect(yaml).toContain("description: 'Orders: one row per order'")
  })

  it('emits an empty datasets list when none are added', () => {
    const yaml = draftToOssieYaml(draft())
    expect(yaml).toContain('datasets:')
    expect(yaml).toContain('  []')
  })

  it('emits relationships only when present', () => {
    const withoutRel = draftToOssieYaml(draft())
    expect(withoutRel).not.toContain('relationships:')

    const withRel = draftToOssieYaml(
      draft({
        relationships: [
          {
            id: newId('r'),
            name: 'orders_to_customers',
            from: 'orders',
            to: 'customers',
            fromColumns: 'customer_id',
            toColumns: 'customer_id',
          },
        ],
      })
    )
    expect(withRel).toContain('relationships:')
    expect(withRel).toContain('from_columns: [customer_id]')
    expect(withRel).toContain('to_columns: [customer_id]')
  })

  it('is deterministic for the same draft', () => {
    const d = draft({
      metrics: [
        {
          id: newId('m'),
          name: 'm1',
          expression: 'SUM(x)',
          datatype: 'Decimal',
          description: '',
        },
      ],
    })
    expect(draftToOssieYaml(d)).toBe(draftToOssieYaml(d))
  })

  it('falls back to a placeholder name when empty', () => {
    const yaml = draftToOssieYaml(draft({ name: '' }))
    expect(yaml).toContain('name: untitled_model')
  })
})
