import { useEffect, useRef } from 'react'
import Editor, { type Monaco } from '@monaco-editor/react'
import { Button } from '@/components/ui/button'
import { SearchableSelect } from '@/components/ui/searchable-select'
import { useTheme } from '@/context/theme-provider'
import { applyNovaSqlTheme } from '@/features/workspaces/monaco-theme'
import { useAuthStore } from '@/stores/auth-store'
import type { SqlSelection } from './contracts'
import { useDatasetDatabases } from './dataset-source-queries'

export function DatasetSqlEditor({ source, disabled, onChange }: {
  source: SqlSelection
  disabled?: boolean
  onChange: (source: SqlSelection) => void
}) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const databases = useDatasetDatabases(epoch)
  const { resolvedTheme } = useTheme()
  const monaco = useRef<Monaco | null>(null)
  useEffect(() => {
    if (monaco.current) applyNovaSqlTheme(monaco.current, resolvedTheme === 'dark')
  }, [resolvedTheme])

  return <div className='min-w-0 space-y-3'>
    <SearchableSelect label='SQL database' value={source.database} options={databases.data ?? []}
      allowEmpty={false} emptyLabel='Choose a database' className='h-11 w-full justify-start sm:h-9'
      disabled={disabled || databases.isPending || databases.isError}
      onChange={database => onChange({ ...source, database })} />
    {databases.isPending ? <p role='status' className='text-sm text-muted-foreground'>Loading databases…</p>
      : databases.isError ? <div role='alert' className='space-y-2 text-sm'>
          <p className='text-destructive'>Databases could not be loaded with your current access.</p>
          <Button type='button' variant='outline' disabled={disabled || databases.isFetching}
            onClick={() => void databases.refetch()}>Retry loading databases</Button>
        </div>
        : !databases.data?.length ? <p role='status' className='text-sm text-muted-foreground'>No databases are available with your current role.</p> : null}
    <div className='h-64 min-w-0 overflow-hidden rounded-md border'>
      <Editor language='sql' defaultValue={source.sql} height='100%' theme={resolvedTheme === 'dark' ? 'nova-dark' : 'nova-light'}
        beforeMount={instance => { monaco.current = instance; applyNovaSqlTheme(instance, resolvedTheme === 'dark') }}
        onChange={sql => onChange({ ...source, sql: sql ?? '' })}
        options={{ ariaLabel: 'Dataset SQL', automaticLayout: true, readOnly: disabled,
          minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false,
          wordWrap: 'on', tabSize: 2, fixedOverflowWidgets: true }} />
    </div>
    <p className='text-xs text-muted-foreground'>Use one read-only SELECT or WITH query. Inspect columns before saving. Rows load only when you run a preview.</p>
  </div>
}
