import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { SearchableSelect } from '@/components/ui/searchable-select'
import { metadataApi } from '@/features/agents/metadata-api'
import { useAuthStore } from '@/stores/auth-store'
import type { ObjectSelection } from './contracts'
import { useDatasetDatabases } from './dataset-source-queries'

type PickerProps = {
  disabled?: boolean
  onSelect: (source: ObjectSelection | null) => void
}

export function DatasetObjectPicker(props: PickerProps) {
  const securityEpoch = useAuthStore(state => state.securityEpoch)
  return <ObjectPicker key={securityEpoch} {...props} securityEpoch={securityEpoch} />
}

function ObjectPicker({ disabled, onSelect, securityEpoch }: PickerProps & { securityEpoch: number }) {
  const [database, setDatabase] = useState('')
  const [object, setObject] = useState('')
  const databases = useDatasetDatabases(securityEpoch)
  const objects = useQuery({
    queryKey: ['native-dataviz', 'source-objects', securityEpoch, database],
    queryFn: ({ signal }) => metadataApi.listTables(database, signal),
    enabled: Boolean(database),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  const relations = [
    ...(objects.data?.tables ?? []).map(item => ({ name: item.name, kind: 'table' as const })),
    ...(objects.data?.views ?? []).map(item => ({ name: item.name, kind: 'view' as const })),
  ]
  const names = [...new Set(relations.map(item => item.name))].sort((a, b) => a.localeCompare(b))

  return <div className='min-w-0 space-y-3'>
    <div className='grid gap-3 sm:grid-cols-2'>
      <div className='min-w-0 space-y-1'>
        <p className='text-sm'>Database</p>
        <SearchableSelect label='Dataset database' value={database} options={databases.data ?? []}
          allowEmpty={false} emptyLabel='Choose a database' className='h-11 w-full justify-start sm:h-9'
          disabled={disabled || databases.isPending || databases.isError}
          onChange={next => { setDatabase(next); setObject(''); onSelect(null) }} />
      </div>
      <div className='min-w-0 space-y-1'>
        <p className='text-sm'>Table or view</p>
        <SearchableSelect label='Dataset table or view' value={object} options={names}
          allowEmpty={false} emptyLabel='Choose a table or view' className='h-11 w-full justify-start sm:h-9'
          disabled={disabled || !database || objects.isPending || objects.isError}
          onChange={name => {
            const matches = relations.filter(item => item.name === name)
            setObject(name)
            onSelect(matches.length === 1 ? {
              kind: matches[0].kind, catalog: 'default_catalog', database, schema_name: 'default',
              object_name: name,
            } : null)
          }} />
      </div>
    </div>
    {databases.isPending || (database && objects.isPending)
      ? <p role='status' className='text-sm text-muted-foreground'>Loading sources…</p>
      : databases.isError || objects.isError
        ? <div role='alert' className='flex flex-wrap items-center gap-2 text-sm'>
            <span className='text-destructive'>Sources could not be loaded with your current access.</span>
            <Button type='button' variant='outline' size='sm' disabled={disabled || databases.isFetching || objects.isFetching}
              onClick={() => { if (databases.isError) void databases.refetch(); else void objects.refetch() }}>
              Retry loading sources
            </Button>
          </div>
        : databases.data?.length === 0 || (database && names.length === 0)
          ? <p role='status' className='text-sm text-muted-foreground'>No sources are available with your current role.</p>
          : null}
    <p className='text-xs text-muted-foreground'>Selecting a source loads its names. Inspect its columns before saving; preview rows when you are ready.</p>
  </div>
}
