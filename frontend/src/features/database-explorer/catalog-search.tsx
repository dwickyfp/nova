import { useEffect, useId, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Columns3, Database, Eye, FolderTree, Layers3, Loader2, Search, Table2 } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { parseCatalogQuery, searchCatalog, type SearchHit } from './catalog-metadata-api'

const ICONS = {
  catalog: FolderTree,
  database: Database,
  table: Table2,
  view: Eye,
  materialized_view: Layers3,
  column: Columns3,
} as const

const MATCH_LABELS: Record<string, string> = {
  name: 'name',
  tag: 'tag',
  comment: 'description',
  owner: 'owner',
  classification: 'classification',
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return debounced
}

/**
 * Search the Nova Catalog by name, tag, description, or owner. `tag:key` or
 * `tag:key=value` filters by tag. Results are the objects the active role can
 * list in the engine; choosing one opens it in the explorer.
 */
export function CatalogSearch({ onSelect }: { onSelect: (hit: SearchHit) => void }) {
  const securityEpoch = useAuthStore((state) => state.securityEpoch)
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const listId = useId()
  const wrapper = useRef<HTMLDivElement>(null)
  const debounced = useDebounced(text, 250)
  const parsed = parseCatalogQuery(debounced)
  const typed = parseCatalogQuery(text)
  const ready = parsed.q.length >= 2 || parsed.tag.length > 0
  const typedReady = typed.q.length >= 2 || typed.tag.length > 0
  const pending = debounced !== text

  const results = useQuery({
    queryKey: ['catalog-search', securityEpoch, parsed.q, parsed.tag],
    queryFn: ({ signal }) => searchCatalog({ q: parsed.q, tag: parsed.tag, limit: 20 }, signal),
    enabled: ready,
    staleTime: 30_000,
    retry: false,
  })
  const hits = ready && !pending ? (results.data?.results ?? []) : []

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (wrapper.current && !wrapper.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const choose = (hit: SearchHit) => {
    onSelect(hit)
    setOpen(false)
  }

  const showPanel = open && text.trim().length > 0

  return (
    <div ref={wrapper} className='relative w-full max-w-md'>
      <Search className='pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground' />
      <Input
        role='combobox'
        aria-label='Search the catalog'
        aria-expanded={showPanel}
        aria-controls={listId}
        aria-activedescendant={showPanel && hits[active] ? `${listId}-${active}` : undefined}
        value={text}
        placeholder='Search catalog, or tag:pii'
        className='h-8 pl-8 text-sm'
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          setText(event.target.value)
          setActive(0)
          setOpen(true)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false)
          else if (event.key === 'ArrowDown' && hits.length) {
            event.preventDefault()
            setActive((index) => Math.min(index + 1, hits.length - 1))
          } else if (event.key === 'ArrowUp' && hits.length) {
            event.preventDefault()
            setActive((index) => Math.max(index - 1, 0))
          } else if (event.key === 'Enter' && hits[active]) {
            event.preventDefault()
            choose(hits[active])
          }
        }}
      />
      {showPanel && (
        <div className='absolute right-0 top-full z-50 mt-1 max-h-96 w-[max(100%,24rem)] max-w-[calc(100vw-2rem)] overflow-y-auto rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-md'>
          {!typedReady ? (
            <p className='px-3 py-2 text-sm text-muted-foreground'>Type at least two characters.</p>
          ) : pending || results.isLoading ? (
            <p className='flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground'>
              <Loader2 className='h-3.5 w-3.5 animate-spin' />
              Searching…
            </p>
          ) : results.error ? (
            <p className='px-3 py-2 text-sm text-destructive'>
              {results.error instanceof Error ? results.error.message : 'Search failed'}
            </p>
          ) : hits.length === 0 ? (
            <p className='px-3 py-2 text-sm text-muted-foreground'>
              Nothing your role can see matches. Try another name, or a tag such as tag:pii.
            </p>
          ) : (
            <ul id={listId} role='listbox' aria-label='Catalog results'>
              {hits.map((hit, index) => {
                const Icon = ICONS[hit.securable_type as keyof typeof ICONS] ?? Table2
                return (
                  <li
                    key={hit.id}
                    id={`${listId}-${index}`}
                    role='option'
                    aria-selected={index === active}
                    className={cn(
                      'flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-sm',
                      index === active && 'bg-muted',
                    )}
                    onMouseEnter={() => setActive(index)}
                    onMouseDown={(event) => {
                      event.preventDefault()
                      choose(hit)
                    }}
                  >
                    <Icon className='mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground' />
                    <div className='min-w-0 flex-1'>
                      <p className='truncate font-medium'>
                        {hit.column_name ?? hit.object_name ?? hit.database_name ?? hit.catalog_name}
                      </p>
                      <p className='truncate text-xs text-muted-foreground'>
                        {hit.full_name}
                        {hit.tags.length > 0 &&
                          ` · ${hit.tags.map((tag) => (tag.value ? `${tag.key}=${tag.value}` : tag.key)).join(', ')}`}
                      </p>
                    </div>
                    <span className='shrink-0 text-xs text-muted-foreground'>
                      {hit.matched.map((item) => MATCH_LABELS[item] ?? item).join(', ')}
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
          {results.data?.truncated && (
            <p className='px-3 py-1.5 text-xs text-muted-foreground'>More results exist; refine the search.</p>
          )}
        </div>
      )}
    </div>
  )
}
