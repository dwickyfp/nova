import { api } from '@/lib/api-client'

/**
 * Models, versions and aliases are scoped to the database they were trained
 * in (a model trained with no database has its own scope). An alias request
 * that leaves out the model's database looks in the wrong scope and fails with
 * "Model version not found" or "alias was not found in this scope".
 */
export type ScopedAlias = {
  alias_name: string
  model_id: string
  model_name: string | null
  version: number
  created_at: string | null
  database_name: string | null
}

type AliasRow = Omit<ScopedAlias, 'database_name'>

/** The query string that selects one database scope, or '' for no database. */
export function scopeQuery(databaseName: string | null | undefined): string {
  return databaseName
    ? `?database_name=${encodeURIComponent(databaseName)}`
    : ''
}

/** Aliases from every database the caller's models live in, tagged with it. */
export async function listScopedAliases(
  models: { database_name?: string | null }[]
): Promise<ScopedAlias[]> {
  const scopes = Array.from(
    new Set<string | null>([null, ...models.map((m) => m.database_name || null)])
  )
  const pages = await Promise.all(
    scopes.map(async (database_name) => {
      const res = await api.get<{ aliases: AliasRow[] }>(
        `/ml/aliases${scopeQuery(database_name)}`
      )
      return res.aliases.map((alias) => ({ ...alias, database_name }))
    })
  )
  return pages.flat()
}

/** A stable key for an alias: the same name may exist in two databases. */
export function aliasKey(alias: Pick<ScopedAlias, 'alias_name' | 'database_name'>): string {
  return `${alias.database_name ?? ''}\u0000${alias.alias_name}`
}

/**
 * Feature values typed into the form: numbers stay numbers and anything else
 * (a plan name, a region) is sent as text, so categorical features are not
 * turned into empty values.
 */
export function parseFeatureValue(raw: string): number | string {
  const value = raw.trim()
  return /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(value) ? Number(value) : value
}
