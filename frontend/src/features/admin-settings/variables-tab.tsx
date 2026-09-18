import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertCircle, Pencil, RotateCcw, SearchX, Sliders } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  LoadingLines,
  RefreshBanner,
} from '@/components/ui/loading-overlay'
import { StatusBadge } from '@/components/ui/status-badge'
import {
  SimpleTablePagination,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls'
import { cn } from '@/lib/utils'
import {
  fetchVariables,
  isVariableChanged,
  setVariable,
  type VariableItem,
  type VariableScope,
} from './api'

export function VariablesTab() {
  const queryClient = useQueryClient()
  const [scope, setScope] = useState<VariableScope>('session')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [editing, setEditing] = useState<VariableItem | null>(null)
  const [draftValue, setDraftValue] = useState('')

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim())
      setPage(1)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [search])

  const variablesQuery = useQuery({
    queryKey: ['variables', scope, debouncedSearch, page, pageSize],
    queryFn: () =>
      fetchVariables({
        scope,
        search: debouncedSearch,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      }),
    placeholderData: keepPreviousData,
  })

  useEffect(() => {
    if (variablesQuery.error) {
      toast.error('Failed to load variables', {
        description: variablesQuery.error.message,
      })
    }
  }, [variablesQuery.error])

  const updateMutation = useMutation({
    mutationFn: (variable: VariableItem) => {
      const value = draftValue.trim()
      return setVariable(
        value === ''
          ? { scope, name: variable.name, reset: true }
          : { scope, name: variable.name, value }
      )
    },
    onSuccess: (updated) => {
      toast.success(updated.message)
      setEditing(null)
      queryClient.invalidateQueries({ queryKey: ['variables'] })
    },
    onError: (err: Error) =>
      toast.error('Could not set variable', { description: err.message }),
  })

  const items = variablesQuery.data?.variables ?? []
  const total = variablesQuery.data?.total ?? 0
  const changedCount = useMemo(
    () => items.filter(isVariableChanged).length,
    [items]
  )

  return (
    <div className='space-y-5'>
      <div className='flex flex-wrap items-center gap-2'>
        <div
          role='group'
          aria-label='Variable scope'
          className='inline-flex rounded-md border border-border p-0.5'
        >
          {(['session', 'global'] as const).map((option) => (
            <button
              key={option}
              type='button'
              aria-pressed={scope === option}
              onClick={() => {
                setScope(option)
                setPage(1)
              }}
              className={cn(
                'rounded px-3 py-1.5 text-sm capitalize transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                scope === option
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-muted'
              )}
            >
              {option}
            </button>
          ))}
        </div>
        <Input
          placeholder='Search variables...'
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className='h-9 w-full sm:max-w-xs'
          aria-label='Search variables'
        />
        <div className='ml-auto flex items-center gap-3 text-sm text-muted-foreground'>
          {changedCount > 0 ? (
            <StatusBadge tone='warning'>
              {changedCount} changed
            </StatusBadge>
          ) : null}
          <span>
            {total} {total === 1 ? 'variable' : 'variables'}
          </span>
        </div>
      </div>

      <SimpleTableViewport>
        {variablesQuery.isFetching && !variablesQuery.isLoading ? (
          <RefreshBanner label='Loading variables...' />
        ) : null}
        <table className='w-full'>
          <thead>
            <tr>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Name
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Value
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Default
              </th>
              <th className='w-24 px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {variablesQuery.isLoading ? (
              <tr>
                <td colSpan={4} className='px-4 py-6'>
                  <LoadingLines rows={6} />
                </td>
              </tr>
            ) : variablesQuery.isError ? (
              <tr>
                <td colSpan={4} className='px-4 py-6'>
                  <EmptyState
                    variant='error'
                    icon={AlertCircle}
                    title='Could not load variables'
                    description='The variables API did not respond. Check the connection and retry.'
                    action={
                      <Button
                        variant='outline'
                        size='sm'
                        onClick={() => void variablesQuery.refetch()}
                      >
                        Retry
                      </Button>
                    }
                  />
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={4} className='px-4 py-6'>
                  <EmptyState
                    icon={SearchX}
                    title={
                      debouncedSearch
                        ? 'No variables match this search'
                        : `No ${scope} variables reported`
                    }
                    description={
                      debouncedSearch
                        ? 'Clear the search box to list every variable in this scope.'
                        : 'The engine returned an empty list for this scope.'
                    }
                    action={
                      debouncedSearch ? (
                        <Button
                          variant='outline'
                          size='sm'
                          onClick={() => setSearch('')}
                        >
                          Clear search
                        </Button>
                      ) : undefined
                    }
                  />
                </td>
              </tr>
            ) : (
              items.map((variable) => {
                const changed = isVariableChanged(variable)
                return (
                  <tr
                    key={variable.name}
                    className='border-b border-border transition-colors last:border-0 hover:bg-muted/50'
                  >
                    <td className='px-4 py-3'>
                      <span className='font-mono text-xs'>{variable.name}</span>
                    </td>
                    <td className='px-4 py-3'>
                      <span
                        className={cn(
                          'font-mono text-xs',
                          changed ? 'font-semibold text-warning-strong' : 'text-foreground'
                        )}
                      >
                        {variable.value || '-'}
                      </span>
                    </td>
                    <td className='px-4 py-3 font-mono text-xs text-muted-foreground'>
                      {variable.default ?? '-'}
                    </td>
                    <td className='px-4 py-3 text-right'>
                      <div className='flex items-center justify-end gap-1'>
                        {changed ? (
                          <Button
                            variant='ghost'
                            size='sm'
                            className='h-8 gap-1 px-2 text-xs text-muted-foreground'
                            aria-label={`Reset ${variable.name} to its default`}
                            onClick={() => {
                              setDraftValue('')
                              setEditing(variable)
                            }}
                          >
                            <RotateCcw className='size-3.5' />
                            Reset
                          </Button>
                        ) : null}
                        <Button
                          variant='ghost'
                          size='sm'
                          className='size-8 p-0 text-muted-foreground'
                          aria-label={`Edit ${variable.name}`}
                          onClick={() => {
                            setDraftValue(variable.value)
                            setEditing(variable)
                          }}
                        >
                          <Pencil className='size-3.5' />
                        </Button>
                      </div>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </SimpleTableViewport>

      <SimpleTablePagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(value) => {
          setPageSize(value)
          setPage(1)
        }}
      />

      <Dialog
        open={Boolean(editing)}
        onOpenChange={(open) => {
          if (!open) setEditing(null)
        }}
      >
        <DialogContent className='sm:max-w-md'>
          <DialogHeader>
            <DialogTitle>Set {editing?.name}</DialogTitle>
            <DialogDescription>
              Applies to the <span className='capitalize'>{scope}</span> scope. Leave
              blank to reset to the engine default.
            </DialogDescription>
          </DialogHeader>
          <div className='grid gap-1.5 py-2'>
            <Label htmlFor='variable-value'>Value</Label>
            <Input
              id='variable-value'
              value={draftValue}
              onChange={(event) => setDraftValue(event.target.value)}
              autoComplete='off'
              className='font-mono'
            />
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button
              disabled={updateMutation.isPending}
              onClick={() => {
                if (editing) updateMutation.mutate(editing)
              }}
            >
              {updateMutation.isPending ? 'Saving...' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <p className='flex items-center gap-1.5 text-xs text-muted-foreground'>
        <Sliders className='size-3.5' />
        Session values apply to your connection only; global values affect every
        session on this engine.
      </p>
    </div>
  )
}
