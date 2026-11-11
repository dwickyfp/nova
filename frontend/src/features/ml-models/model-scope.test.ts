import { beforeEach, describe, expect, it, vi } from 'vitest'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api-client', () => ({ api: { get } }))

import { aliasKey, listScopedAliases, parseFeatureValue, scopeQuery } from './model-scope'

describe('ML model scope', () => {
  beforeEach(() => get.mockReset())

  it('lists aliases from every database a model lives in, tagged with it', async () => {
    get.mockImplementation(async (...args: unknown[]) => {
      return {
        aliases: String(args[0]).endsWith('?database_name=SALES')
          ? [{ alias_name: 'churn', model_id: 'm1', model_name: 'churn_model', version: 2, created_at: null }]
          : [],
      }
    })
    const aliases = await listScopedAliases([
      { database_name: 'SALES' },
      { database_name: 'SALES' },
      { database_name: null },
    ])
    expect(get.mock.calls.map(([path]) => path).sort()).toEqual([
      '/ml/aliases',
      '/ml/aliases?database_name=SALES',
    ])
    expect(aliases).toEqual([
      expect.objectContaining({ alias_name: 'churn', database_name: 'SALES' }),
    ])
  })

  it('keeps the same alias name in two databases apart', () => {
    expect(aliasKey({ alias_name: 'a', database_name: 'X' })).not.toBe(
      aliasKey({ alias_name: 'a', database_name: null })
    )
    expect(scopeQuery(null)).toBe('')
    expect(scopeQuery('A B')).toBe('?database_name=A%20B')
  })

  it('sends numbers as numbers and categories as text', () => {
    expect(parseFeatureValue(' 42 ')).toBe(42)
    expect(parseFeatureValue('-1.5e2')).toBe(-150)
    expect(parseFeatureValue('basic')).toBe('basic')
    expect(parseFeatureValue('12 months')).toBe('12 months')
  })
})
