/**
 * The sources a Nova catalog can read, as the console presents them.
 *
 * One descriptor per backend `CatalogSource` subclass
 * (`backend/app/modules/catalog/sources/`). A descriptor says which address
 * fields the source needs and how they become the connection's endpoint, and
 * how its credential is named. Adding a source is one entry here and one
 * subclass there.
 */

export type SourceType = 'starrocks' | 'postgres' | 'snowflake' | 'iceberg' | 'kafka'

export type SourceField = {
  key: string
  label: string
  placeholder: string
  required: boolean
  /** Kept as typed: Snowflake identifiers are case-sensitive when quoted. */
  hint?: string
  numeric?: boolean
  /** A fixed list of values, shown as a select. The first is the default. */
  choices?: string[]
  /** Shown only when this returns true for the current values. */
  visible?: (values: Record<string, string>) => boolean
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
  /** False for a source that feeds loads instead of backing a catalog (Kafka). */
  catalog: boolean
  /** Whether these address values need a credential; always when absent. */
  credentialsRequired?: (values: Record<string, string>) => boolean
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
    catalog: true,
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
    catalog: true,
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
    catalog: true,
  },
  {
    type: 'iceberg',
    label: 'Iceberg',
    summary: 'An Iceberg REST catalog; its namespaces become the catalog’s databases.',
    fields: [
      {
        key: 'uri', label: 'Catalog URI', placeholder: 'http://iceberg-rest:8181', required: true,
        hint: 'The REST catalog service, as the engine reaches it.',
      },
      {
        key: 'warehouse', label: 'Warehouse', placeholder: 'Location or name', required: false,
        hint: 'Leave empty when the catalog service sets it.',
      },
      {
        key: 's3_endpoint', label: 'Storage endpoint', placeholder: 'http://object-store:9000',
        required: false,
        hint: 'For self-hosted S3-compatible storage. Leave empty for Amazon S3.',
      },
      { key: 's3_region', label: 'Storage region', placeholder: 'us-east-1', required: false },
    ],
    usernameLabel: 'Access key ID',
    passwordLabel: 'Secret access key',
    passwordHint: 'A storage key limited to the warehouse bucket.',
    toEndpoint: (values) => {
      const params = new URLSearchParams()
      for (const key of ['warehouse', 's3_endpoint', 's3_region']) {
        const value = trimmed(values, key)
        if (value) params.set(key, value)
      }
      const query = params.toString()
      return query ? `${trimmed(values, 'uri')}?${query}` : trimmed(values, 'uri')
    },
    catalog: true,
  },
  {
    type: 'kafka',
    label: 'Kafka',
    summary: 'A Kafka cluster that routine loads read topics from.',
    fields: [
      {
        key: 'brokers', label: 'Brokers', placeholder: 'kafka-1:9092,kafka-2:9092', required: true,
        hint: 'host:port, separated by commas, as the engine reaches them.',
      },
      {
        key: 'security_protocol', label: 'Security protocol', placeholder: 'PLAINTEXT',
        required: false, choices: ['PLAINTEXT', 'SSL', 'SASL_PLAINTEXT', 'SASL_SSL'],
      },
      {
        key: 'sasl_mechanism', label: 'SASL mechanism', placeholder: 'PLAIN', required: false,
        choices: ['PLAIN', 'SCRAM-SHA-256', 'SCRAM-SHA-512'],
        visible: (values) => kafkaUsesSasl(values),
      },
    ],
    usernameLabel: 'SASL username',
    passwordLabel: 'SASL password',
    toEndpoint: (values) => {
      const params = new URLSearchParams()
      const protocol = trimmed(values, 'security_protocol') || 'PLAINTEXT'
      if (protocol !== 'PLAINTEXT') params.set('security_protocol', protocol)
      if (kafkaUsesSasl(values)) {
        params.set('sasl_mechanism', trimmed(values, 'sasl_mechanism') || 'PLAIN')
      }
      const brokers = trimmed(values, 'brokers').replace(/\s+/g, '')
      const query = params.toString()
      return query ? `${brokers}?${query}` : brokers
    },
    catalog: false,
    credentialsRequired: (values) => kafkaUsesSasl(values),
  },
]

function kafkaUsesSasl(values: Record<string, string>) {
  return trimmed(values, 'security_protocol').startsWith('SASL')
}

/** Sources a catalog can be built from. */
export const CATALOG_SOURCES = (): SourceDescriptor[] => SOURCES.filter((s) => s.catalog)

/** Whether a connection with these address values needs a credential. */
export function needsCredentials(source: SourceDescriptor, values: Record<string, string>) {
  return source.credentialsRequired ? source.credentialsRequired(values) : true
}

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
    if (field.visible && !field.visible(values)) return true
    const value = trimmed(values, field.key)
    if (field.required && !value) return false
    if (field.numeric && value && !/^\d{1,5}$/.test(value)) return false
    return true
  })
}
