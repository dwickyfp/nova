import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  RefreshCw,
  XCircle,
  CheckCircle,
  Zap,
  AlertTriangle,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

type ActiveQuery = {
  id: number
  user: string
  host: string
  db: string | null
  command: string
  time: number
  state: string
  info: string | null
  query_id: string | null
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return s > 0 ? `${m}m ${s}s` : `${m}m`
  }
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}

function getCommandColor(command: string): string {
  const cmd = command.toLowerCase()
  if (cmd === 'query') return 'bg-warning/10 text-warning border-warning/20'
  if (cmd === 'sleep') return 'text-muted-foreground'
  return 'bg-info/10 text-info border-info/20'
}

export function MonitoringActiveQueries() {
  const queryClient = useQueryClient()
  const [userFilter, setUserFilter] = useState('')
  const [databaseFilter, setDatabaseFilter] = useState('')
  const [killTarget, setKillTarget] = useState<ActiveQuery | null>(null)

  const { data, isLoading, isFetching, refetch } = useQuery<ActiveQuery[]>({
    queryKey: ['monitoring', 'active-queries'],
    queryFn: () => api.get<ActiveQuery[]>('/monitoring/queries/active'),
    refetchInterval: 5000,
  })

  const killMutation = useMutation({
    mutationFn: (query: ActiveQuery) =>
      api.post('/monitoring/queries/kill', { connection_id: query.id }),
    onSuccess: () => {
      toast.success('Query killed successfully')
      queryClient.invalidateQueries({
        queryKey: ['monitoring', 'active-queries'],
      })
      setKillTarget(null)
    },
    onError: (error: Error) => {
      toast.error(`Failed to kill query: ${error.message}`)
      setKillTarget(null)
    },
  })

  const queries = data ?? []
  const filtered = queries.filter((q) => {
    if (userFilter && !q.user.toLowerCase().includes(userFilter.toLowerCase()))
      return false
    if (
      databaseFilter &&
      !(q.db ?? '').toLowerCase().includes(databaseFilter.toLowerCase())
    )
      return false
    return true
  })

  return (
    <div className='space-y-4'>
      <div className='flex items-center justify-between'>
        <div>
          <h3 className='text-lg font-medium'>Active Queries</h3>
          <p className='text-sm text-muted-foreground'>
            View currently running queries and their execution status.
          </p>
        </div>
        <div className='flex items-center gap-2'>
          <Badge variant='secondary' className='gap-1'>
            <Zap className='h-3 w-3' />
            {queries.length} {queries.length === 1 ? 'connection' : 'connections'}
          </Badge>
          {isFetching && (
            <RefreshCw className='h-4 w-4 animate-spin text-muted-foreground' />
          )}
          <Button
            variant='outline'
            size='sm'
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <RefreshCw
              className={cn('h-4 w-4', isFetching && 'animate-spin')}
            />
            Refresh
          </Button>
        </div>
      </div>

      <div className='flex gap-2'>
        <Input
          placeholder='Filter by user...'
          value={userFilter}
          onChange={(e) => setUserFilter(e.target.value)}
          className='max-w-xs'
        />
        <Input
          placeholder='Filter by database...'
          value={databaseFilter}
          onChange={(e) => setDatabaseFilter(e.target.value)}
          className='max-w-xs'
        />
      </div>

      {isLoading ? (
        <div className='rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground'>
          Loading...
        </div>
      ) : filtered.length === 0 ? (
        <div className='rounded-md border border-dashed p-8 text-center'>
          <CheckCircle className='mx-auto h-8 w-8 text-muted-foreground' />
          <p className='mt-2 text-sm text-muted-foreground'>No active queries</p>
        </div>
      ) : (
        <div className='rounded-md border'>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ID</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Host</TableHead>
                <TableHead>Database</TableHead>
                <TableHead>Command</TableHead>
                <TableHead>Time</TableHead>
                <TableHead>State</TableHead>
                <TableHead>Query</TableHead>
                <TableHead className='text-right'>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((query) => (
                <TableRow key={query.id}>
                  <TableCell className='font-mono text-xs'>
                    {query.id}
                  </TableCell>
                  <TableCell>{query.user}</TableCell>
                  <TableCell className='font-mono text-xs'>
                    {query.host}
                  </TableCell>
                  <TableCell>{query.db ?? '—'}</TableCell>
                  <TableCell>
                    <span
                      className={cn(
                        'inline-block rounded px-2 py-0.5 text-xs font-medium',
                        getCommandColor(query.command)
                      )}
                    >
                      {query.command}
                    </span>
                  </TableCell>
                  <TableCell className='font-mono text-xs'>
                    {formatDuration(query.time ?? 0)}
                  </TableCell>
                  <TableCell className='text-xs'>{query.state}</TableCell>
                  <TableCell className='max-w-xs truncate font-mono text-xs text-muted-foreground'>
                    {query.info ?? '—'}
                  </TableCell>
                  <TableCell className='text-right'>
                    <Button
                      variant='ghost'
                      size='sm'
                      onClick={() => setKillTarget(query)}
                      className='text-destructive hover:text-destructive'
                    >
                      <XCircle className='h-4 w-4' />
                      Kill
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={!!killTarget} onOpenChange={() => setKillTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className='flex items-center gap-2'>
              <AlertTriangle className='h-5 w-5 text-destructive' />
              Kill Query
            </DialogTitle>
            <DialogDescription>
              Are you sure you want to kill this query? This action cannot be
              undone.
            </DialogDescription>
          </DialogHeader>
          {killTarget && (
            <div className='rounded-md bg-muted p-3 text-sm'>
              <p>
                <span className='font-medium'>Connection ID:</span> {killTarget.id}
              </p>
              <p>
                <span className='font-medium'>User:</span> {killTarget.user}
              </p>
              <p>
                <span className='font-medium'>Query:</span>{' '}
                <code className='text-xs'>
                  {killTarget.info?.slice(0, 100) ?? 'N/A'}
                  {(killTarget.info?.length ?? 0) > 100 ? '...' : ''}
                </code>
              </p>
            </div>
          )}
          <DialogFooter>
            <Button variant='outline' onClick={() => setKillTarget(null)}>
              Cancel
            </Button>
            <Button
              variant='destructive'
              onClick={() => killTarget && killMutation.mutate(killTarget)}
              disabled={killMutation.isPending}
            >
              {killMutation.isPending ? 'Killing...' : 'Kill Query'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

