/**
 * The sources a Nova catalog can read, as the console presents them.
 *
 * One descriptor per backend `CatalogSource` subclass
 * (`backend/app/modules/catalog/sources/`). A descriptor says which address
 * fields the source needs and how they become the connection's endpoint, and
 * how its credential is named. Adding a source is one entry here and one
 * subclass there.
 */

export type SourceType = 'starrocks' | 'postgres' | 'snowflake'

export type SourceField = {
  key: string
  label: string
  placeholder: string
  required: boolean
  /** Kept as typed: Snowflake identifiers are case-sensitive when quoted. */
  hint?: string
  numeric?: boolean
}

export type SourceDescriptor = {
  type: SourceType
  label: string
  /** One line under the type choice. */
  summary: string
  fields: SourceField[]
  usernameLabel: string
  passwordLabel: string
  passwordHint?: string
  toEndpoint: (values: Record<string, string>) => string
}

const trimmed = (values: Record<string, string>, key: string) => (values[key] ?? '').trim()

function hostPort(values: Record<string, string>) {
  const host = trimmed(values, 'host')
  const port = trimmed(values, 'port')
  return port ? `${host}:${port}` : host
}

export const SOURCES: SourceDescriptor[] = [
  {
    type: 'starrocks',
    label: 'StarRocks',
    summary: 'Another StarRocks cluster, read through its FE query port.',
    fields: [
      { key: 'host', label: 'FE host', placeholder: 'starrocks-fe.internal', required: true },
      { key: 'port', label: 'Query port', placeholder: '9030', required: false, numeric: true },
    ],
    usernameLabel: 'Username',
    passwordLabel: 'Password',
    toEndpoint: hostPort,
  },
  {
    type: 'postgres',
    label: 'PostgreSQL',
    summary: 'One PostgreSQL database; its schemas become the catalog’s databases.',
    fields: [
      { key: 'host', label: 'Host', placeholder: 'db.internal', required: true },
      { key: 'port', label: 'Port', placeholder: '5432', required: false, numeric: true },
      { key: 'database', label: 'Database', placeholder: 'sales', required: true },
    ],
    usernameLabel: 'Username',
    passwordLabel: 'Password',
    toEndpoint: (values) => `${hostPort(values)}/${trimmed(values, 'database')}`,
  },
  {
    type: 'snowflake',
    label: 'Snowflake',
    summary: 'One Snowflake database; queries run on the warehouse you name.',
    fields: [
      {
        key: 'account', label: 'Account', placeholder: 'ORGNAME-ACCOUNT', required: true,
        hint: 'The account identifier, without .snowflakecomputing.com.',
      },
      { key: 'database', label: 'Database', placeholder: 'ANALYTICS', required: true },
      { key: 'warehouse', label: 'Warehouse', placeholder: 'COMPUTE_WH', required: true },
      { key: 'role', label: 'Role', placeholder: 'Default role', required: false },
    ],
    usernameLabel: 'User',
    passwordLabel: 'Programmatic access token',
    passwordHint: 'Create one in Snowsight under the user’s settings; restrict it to a read-only role.',
    toEndpoint: (values) => {
      const params = new URLSearchParams({ warehouse: trimmed(values, 'warehouse') })
      const role = trimmed(values, 'role')
      if (role) params.set('role', role)
      return `${trimmed(values, 'account')}/${trimmed(values, 'database')}?${params.toString()}`
    },
  },
]

export function sourceFor(type: string | null | undefined): SourceDescriptor | undefined {
  const key = type === 'postgresql' ? 'postgres' : type
  return SOURCES.find((s) => s.type === key)
}

export function sourceLabel(type: string | null | undefined): string {
  return sourceFor(type)?.label ?? (type ? type.charAt(0).toUpperCase() + type.slice(1) : '—')
}

/** Every required field is filled, and numeric fields hold a valid port. */
export function addressComplete(source: SourceDescriptor, values: Record<string, string>) {
  return source.fields.every((field) => {
    const value = trimmed(values, field.key)
    if (field.required && !value) return false
    if (field.numeric && value && !/^\d{1,5}$/.test(value)) return false
    return true
  })
}
