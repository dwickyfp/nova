import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { SearchableSelect } from '@/components/ui/searchable-select'
import { metadataApi } from '@/features/agents/metadata-api'

export function RelationField({ label, value, onChange }: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  const [database, setDatabase] = useState(value.split('.')[0] || '')
  const databases = useQuery({
    queryKey: ['intelligence', 'databases'],
    queryFn: async () => {
      const result = await metadataApi.listDatabases(true)
      return result
    },
  })
  const objects = useQuery({
    queryKey: ['intelligence', 'relations', database],
    queryFn: () => metadataApi.listTables(database),
    enabled: Boolean(database),
  })
  const relations = [
    ...(objects.data?.tables ?? []).map((item) => item.name),
    ...(objects.data?.views ?? []).map((item) => item.name),
  ].sort((a, b) => a.localeCompare(b))
  const selected = value.startsWith(`${database}.`) ? value.slice(database.length + 1) : ''

  return <div className="space-y-1 text-sm">
    <span>{label}</span>
    <div className="grid gap-2 sm:grid-cols-2">
      <SearchableSelect label={`${label} database`} options={databases.data ?? []}
        value={database} allowEmpty={false} emptyLabel="Choose database"
        className="h-9 w-full justify-start"
        onChange={(next) => { setDatabase(next); onChange('') }} />
      <SearchableSelect label={`${label} relation`} options={relations}
        value={selected} allowEmpty={false} emptyLabel="Choose table or view"
        disabled={!database || objects.isPending} className="h-9 w-full justify-start"
        onChange={(next) => onChange(`${database}.${next}`)} />
    </div>
    {objects.isError && <p role="alert" className="text-xs text-destructive">Could not load relations.</p>}
  </div>
}

export function ColumnField({ label, value, onChange, options, multiple = false }: {
  label: string
  value: string
  onChange: (value: string) => void
  options: string[]
  multiple?: boolean
}) {
  if (!multiple) {
    return <div className="space-y-1 text-sm">
      <span>{label}</span>
      <SearchableSelect label={label} value={value} onChange={onChange}
        options={options} allowEmpty={false} emptyLabel="Choose column"
        disabled={!options.length} className="h-9 w-full justify-start" />
    </div>
  }
  const selected = value.split(',').map((item) => item.trim()).filter(Boolean)
  const toggle = (column: string) => {
    onChange((selected.includes(column)
      ? selected.filter((item) => item !== column)
      : [...selected, column]).join(', '))
  }
  return <div className="space-y-1 text-sm">
    <span>{label}</span>
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" disabled={!options.length}
          aria-label={label} className="h-9 w-full justify-between font-normal">
          <span className="truncate">{selected.length ? selected.join(', ') : 'Choose columns'}</span>
          <ChevronDown className="size-4 shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="max-h-64 w-64 overflow-y-auto p-2">
        {options.map((column) => <label key={column}
          className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-muted">
          <Checkbox checked={selected.includes(column)}
            onCheckedChange={() => toggle(column)} />
          <span className="truncate">{column}</span>
        </label>)}
      </PopoverContent>
    </Popover>
  </div>
}
