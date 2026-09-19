import { useCallback, useEffect, useState } from 'react'
import { ExternalLink, Loader2, MoreHorizontal, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Header } from '@/components/layout/header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { fetchExternalCatalogs, type ExternalCatalog } from './api'
import { CreateCatalogDialog, DropCatalogDialog } from './catalog-dialogs'

export function ExternalCatalogsPage() {
  const [catalogs, setCatalogs] = useState<ExternalCatalog[]>([])
  const [loading, setLoading] = useState(true)
  const [createOpen, setCreateOpen] = useState(false)
  const [dropTarget, setDropTarget] = useState<ExternalCatalog | null>(null)
  const [selected, setSelected] = useState<ExternalCatalog | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetchExternalCatalogs()
      setCatalogs(res.catalogs)
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to load external catalogs'
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className='flex h-full min-h-0 flex-col'>
      <Header fixed>
        <div className='flex min-w-0 flex-1 items-center gap-3'>
          <div className='min-w-0'>
            <h1 className='truncate text-lg font-semibold'>External Catalogs</h1>
            <p className='text-sm text-muted-foreground'>
              Iceberg and Hive catalogs backed by a storage connection
            </p>
          </div>
        </div>
        <div className='ml-auto flex items-center gap-2'>
          <Button
            variant='outline'
            size='icon'
            className='h-8 w-8'
            onClick={load}
            disabled={loading}
            aria-label='Refresh'
          >
            <RefreshCw className={loading ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
          </Button>
          <Button size='sm' className='h-8' onClick={() => setCreateOpen(true)}>
            <Plus className='mr-1.5 h-3.5 w-3.5' />
            Add Catalog
          </Button>
        </div>
      </Header>

      <div className='min-h-0 flex-1 overflow-auto p-4'>
        <div className='rounded-lg border border-border bg-background'>
          <table className='w-full text-sm'>
            <thead className='bg-muted'>
              <tr className='text-left text-xs text-muted-foreground'>
                <th className='px-4 py-2 font-medium'>Name</th>
                <th className='px-4 py-2 font-medium'>Type</th>
                <th className='px-4 py-2 font-medium'>Metastore</th>
                <th className='px-4 py-2 font-medium'>Storage</th>
                <th className='w-10 px-4 py-2' />
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={5} className='px-4 py-10 text-center'>
                    <Loader2 className='mx-auto h-4 w-4 animate-spin text-muted-foreground' />
                  </td>
                </tr>
              ) : catalogs.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className='px-4 py-10 text-center text-sm text-muted-foreground'
                  >
                    No external catalogs yet.
                  </td>
                </tr>
              ) : (
                catalogs.map((catalog) => (
                  <tr
                    key={catalog.name}
                    className='cursor-pointer border-t border-border hover:bg-muted/50'
                    onClick={() => setSelected(catalog)}
                  >
                    <td className='px-4 py-2 font-medium'>{catalog.name}</td>
                    <td className='px-4 py-2'>
                      <Badge variant='secondary' className='capitalize'>
                        {catalog.type || 'unknown'}
                      </Badge>
                    </td>
                    <td className='px-4 py-2 text-muted-foreground'>
                      {catalog.metastore_uri || '—'}
                    </td>
                    <td className='px-4 py-2 text-muted-foreground'>
                      {catalog.storage_connection || '—'}
                    </td>
                    <td className='px-4 py-2 text-right'>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant='ghost'
                            size='icon'
                            className='h-7 w-7'
                            onClick={(e) => e.stopPropagation()}
                            aria-label={`Actions for ${catalog.name}`}
                          >
                            <MoreHorizontal className='h-3.5 w-3.5' />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align='end'>
                          <DropdownMenuItem
                            onClick={(e) => {
                              e.stopPropagation()
                              setDropTarget(catalog)
                            }}
                          >
                            <Trash2 className='mr-2 h-3.5 w-3.5' />
                            Drop
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {selected && (
          <div className='mt-4 rounded-lg border border-border bg-background p-4'>
            <div className='mb-2 flex items-center gap-2 text-sm font-medium'>
              <ExternalLink className='h-3.5 w-3.5' />
              {selected.name}
            </div>
            <p className='mb-2 text-xs text-muted-foreground'>
              Credentials are never returned here. The statement below is the
              engine&apos;s DDL with every secret value redacted.
            </p>
            <pre className='max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs'>
              {selected.create_statement || 'No DDL available'}
            </pre>
          </div>
        )}
      </div>

      <CreateCatalogDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={() => void load()}
      />

      <DropCatalogDialog
        catalog={dropTarget}
        onOpenChange={(open) => {
          if (!open) setDropTarget(null)
        }}
        onDropped={(name) => {
          if (selected?.name === name) setSelected(null)
          void load()
        }}
      />
    </div>
  )
}
