import { useMemo, useState } from 'react'
import {
  Check,
  Database,
  FolderOpen,
  LayoutGrid,
  Search,
} from 'lucide-react'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { cn } from '@/lib/utils'

type SchemaItem = { name: string }

export type DatabaseSchemaSelectorProps = {
  databases: string[]
  selectedDatabase: string
  schemas: SchemaItem[]
  selectedSchema: string
  onSelectDatabase: (database: string) => void
  onSelectSchema: (schema: string) => void
}

export function DatabaseSchemaSelector({
  databases,
  selectedDatabase,
  schemas,
  selectedSchema,
  onSelectDatabase,
  onSelectSchema,
}: DatabaseSchemaSelectorProps) {
  const [open, setOpen] = useState(false)
  const [dbSearch, setDbSearch] = useState('')
  const [schemaSearch, setSchemaSearch] = useState('')

  const filteredDatabases = useMemo(() => {
    if (!dbSearch) return databases
    const q = dbSearch.toLowerCase()
    return databases.filter((db) => db.toLowerCase().includes(q))
  }, [databases, dbSearch])

  const filteredSchemas = useMemo(() => {
    if (!schemaSearch) return schemas
    const q = schemaSearch.toLowerCase()
    return schemas.filter((s) => s.name.toLowerCase().includes(q))
  }, [schemas, schemaSearch])

  return (
    <Popover open={open} onOpenChange={(v) => {
      setOpen(v)
      if (!v) {
        setDbSearch('')
        setSchemaSearch('')
      }
    }}>
      <PopoverTrigger asChild>
        <button
          type='button'
          className='flex items-center gap-3 rounded-md px-3 py-1.5 text-sm transition-colors hover:bg-muted/60'
        >
          <span className='flex items-center gap-1.5'>
            <Database className='size-3.5 text-muted-foreground' />
            <span className='font-medium'>{selectedDatabase || 'Database'}</span>
          </span>
          <span className='h-3.5 w-px bg-border' />
          <span className='flex items-center gap-1.5'>
            <LayoutGrid className='size-3.5 text-muted-foreground' />
            <span>{selectedSchema || 'Schema'}</span>
          </span>
        </button>
      </PopoverTrigger>

      <PopoverContent
        align='end'
        className='w-[480px] p-0'
        sideOffset={8}
      >
        <div className='flex'>
          {/* ── Left pane: Databases ── */}
          <div className='flex w-1/2 flex-col border-r border-border'>
            <div className='p-2'>
              <div className='relative'>
                <Search className='absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground' />
                <input
                  value={dbSearch}
                  onChange={(e) => setDbSearch(e.target.value)}
                  placeholder='Databases'
                  className='h-7 w-full rounded-md border border-border bg-background pl-8 pr-2 text-xs outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-primary'
                />
              </div>
            </div>
            <div className='max-h-[280px] overflow-auto py-1'>
              {filteredDatabases.length === 0 && (
                <div className='px-3 py-4 text-center text-xs text-muted-foreground'>
                  No databases found
                </div>
              )}
              {filteredDatabases.map((db) => {
                const isActive = db === selectedDatabase
                return (
                  <button
                    key={db}
                    type='button'
                    onClick={() => onSelectDatabase(db)}
                    className={cn(
                      'flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors hover:bg-muted/50',
                      isActive ? 'text-primary' : 'text-foreground'
                    )}
                  >
                    <Database className='size-3.5 shrink-0' />
                    <span className='truncate'>{db}</span>
                    {isActive && (
                      <Check className='ml-auto size-3.5 shrink-0 text-primary' />
                    )}
                  </button>
                )
              })}
            </div>
          </div>

          {/* ── Right pane: Schemas ── */}
          <div className='flex w-1/2 flex-col'>
            <div className='p-2'>
              <div className='relative'>
                <Search className='absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground' />
                <input
                  value={schemaSearch}
                  onChange={(e) => setSchemaSearch(e.target.value)}
                  placeholder='Schemas'
                  className='h-7 w-full rounded-md border border-border bg-background pl-8 pr-2 text-xs outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-primary'
                />
              </div>
            </div>
            <div className='max-h-[280px] overflow-auto py-1'>
              {filteredSchemas.length === 0 && (
                <div className='px-3 py-4 text-center text-xs text-muted-foreground'>
                  {selectedDatabase ? 'No schemas found' : 'Select a database first'}
                </div>
              )}
              {filteredSchemas.map((schema) => {
                const isActive = schema.name === selectedSchema
                return (
                  <button
                    key={schema.name}
                    type='button'
                    onClick={() => {
                      onSelectSchema(schema.name)
                      setOpen(false)
                    }}
                    className={cn(
                      'flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors hover:bg-muted/50',
                      isActive ? 'text-primary' : 'text-foreground'
                    )}
                  >
                    <FolderOpen className='size-3.5 shrink-0' />
                    <span className='truncate'>{schema.name}</span>
                    {isActive && (
                      <Check className='ml-auto size-3.5 shrink-0 text-primary' />
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
