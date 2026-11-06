import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Database,
  KeyRound,
  MoreHorizontal,
  Plug,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines } from '@/components/ui/loading-overlay'
import { StatusBadge } from '@/components/ui/status-badge'
import { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { fetchConnections, type ConnectionSummary } from './api'
import { sourceLabel } from './sources'
import {
  AddCatalogDialog,
  AddConnectionDialog,
  CredentialsDialog,
  DropConnectionDialog,
} from './connection-dialogs'

function typeLabel(value: string) {
  return sourceLabel(value)
}

function CredentialStatus({ connection }: { connection: ConnectionSummary }) {
  if (!connection.has_credentials) return <StatusBadge tone='warning'>Missing</StatusBadge>
  return (
    <StatusBadge tone='neutral'>
      {connection.credential_source === 'managed' ? 'Stored in Nova' : 'Secret store'}
    </StatusBadge>
  )
}

/**
 * External databases that foreign catalogs are built from. Each connection
 * shows its name and type only: addresses and credentials stay on the server.
 */
export function ConnectionsPage() {
  const queryClient = useQueryClient()
  const securityEpoch = useAuthStore((state) => state.securityEpoch)
  const [addOpen, setAddOpen] = useState(false)
  const [catalogFrom, setCatalogFrom] = useState<string | null>(null)
  const [credentialsFor, setCredentialsFor] = useState<ConnectionSummary | null>(null)
  const [dropTarget, setDropTarget] = useState<ConnectionSummary | null>(null)

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ['catalog-connections', securityEpoch],
    queryFn: fetchConnections,
    retry: false,
  })
  const connections = data?.connections ?? []
  const forbidden = error instanceof ApiError && error.status === 403

  const changed = () => {
    void refetch()
    void queryClient.invalidateQueries({ queryKey: ['explorer-catalogs'] })
  }

  return (
    <div data-layout='fixed' className='flex h-full min-h-0 min-w-0 flex-col overflow-hidden'>
      <Header fixed>
        <div className='flex min-w-0 flex-1 items-center gap-3'>
          <div className='min-w-0'>
            <h1 className='truncate text-lg font-heading'>Connections</h1>
            <p className='truncate text-sm text-muted-foreground'>
              Databases that catalogs in{' '}
              <Link to='/database-explorer' className='underline underline-offset-2'>
                Database Explorer
              </Link>{' '}
              are built from
            </p>
          </div>
        </div>
        <div className='ml-auto flex items-center gap-2'>
          <Button
            variant='outline'
            size='icon'
            className='h-8 w-8'
            onClick={() => void refetch()}
            disabled={isFetching}
            aria-label='Reload connections'
          >
            <RefreshCw className={isFetching ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
          </Button>
          <Button
            size='sm'
            className='h-8'
            onClick={() => setAddOpen(true)}
            disabled={forbidden}
            aria-label='Add connection'
          >
            <Plus className='h-3.5 w-3.5 sm:mr-1.5' />
            <span className='hidden sm:inline'>Add connection</span>
          </Button>
        </div>
      </Header>

      <div className='min-h-0 flex-1 overflow-auto p-4'>
        {isLoading ? (
          <LoadingLines rows={4} />
        ) : forbidden ? (
          <EmptyState
            icon={KeyRound}
            title='Connections need a security admin role'
            description='Switch to ACCOUNTADMIN or SECURITYADMIN to see and manage connections.'
          />
        ) : error ? (
          <EmptyState
            variant='error'
            icon={Plug}
            title='Could not load connections'
            description={error instanceof Error ? error.message : 'The request failed.'}
            action={
              <Button variant='outline' size='sm' onClick={() => void refetch()}>
                Try again
              </Button>
            }
          />
        ) : connections.length === 0 ? (
          <EmptyState
            icon={Plug}
            title='No connections yet'
            description='Add a PostgreSQL or MySQL database, then create a catalog from it to query its tables.'
            action={
              <Button size='sm' onClick={() => setAddOpen(true)}>
                <Plus className='mr-1.5 h-3.5 w-3.5' />
                Add connection
              </Button>
            }
          />
        ) : (
          <div className='overflow-x-auto rounded-lg border border-border bg-background'>
            <table className='w-full min-w-[640px] text-sm'>
              <thead className='bg-muted'>
                <tr className='text-left text-xs text-muted-foreground'>
                  <th className='px-4 py-2 font-medium'>Name</th>
                  <th className='px-4 py-2 font-medium'>Type</th>
                  <th className='px-4 py-2 font-medium'>Credentials</th>
                  <th className='px-4 py-2 font-medium'>Catalogs</th>
                  <th className='px-4 py-2 font-medium'>Owner</th>
                  <th className='w-10 px-4 py-2'>
                    <span className='sr-only'>Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {connections.map((connection) => (
                  <tr key={connection.name} className='border-t border-border align-top'>
                    <td className='px-4 py-2'>
                      <div className='font-medium'>{connection.name}</div>
                      {connection.comment ? (
                        <div className='text-xs text-muted-foreground'>{connection.comment}</div>
                      ) : null}
                    </td>
                    <td className='px-4 py-2'>{typeLabel(connection.connector_type)}</td>
                    <td className='px-4 py-2'>
                      <CredentialStatus connection={connection} />
                    </td>
                    <td className='px-4 py-2'>
                      {connection.catalogs.length > 0 ? (
                        <span className='flex flex-wrap gap-1'>
                          {connection.catalogs.map((catalog) => (
                            <span key={catalog} className='inline-flex items-center gap-1'>
                              <Database aria-hidden='true' className='h-3 w-3 text-muted-foreground' />
                              {catalog}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <span className='text-muted-foreground'>None</span>
                      )}
                    </td>
                    <td className='px-4 py-2 text-muted-foreground'>{connection.owner ?? '—'}</td>
                    <td className='px-4 py-2 text-right'>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant='ghost'
                            size='icon'
                            className='h-7 w-7'
                            aria-label={`Actions for ${connection.name}`}
                          >
                            <MoreHorizontal className='h-3.5 w-3.5' />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align='end'>
                          <DropdownMenuItem
                            disabled={!connection.has_credentials}
                            onClick={() => setCatalogFrom(connection.name)}
                          >
                            <Database className='mr-2 h-3.5 w-3.5' />
                            Create catalog
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => setCredentialsFor(connection)}>
                            <KeyRound className='mr-2 h-3.5 w-3.5' />
                            Enter credentials
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => setDropTarget(connection)}>
                            <Trash2 className='mr-2 h-3.5 w-3.5' />
                            Remove
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <AddConnectionDialog open={addOpen} onOpenChange={setAddOpen} onCreated={changed} />
      <AddCatalogDialog
        open={catalogFrom !== null}
        onOpenChange={(open) => {
          if (!open) setCatalogFrom(null)
        }}
        connections={connections}
        initialConnection={catalogFrom ?? undefined}
        onCreated={changed}
        onConnectionCreated={changed}
      />
      <CredentialsDialog
        connection={credentialsFor}
        onOpenChange={(open) => {
          if (!open) setCredentialsFor(null)
        }}
        onSaved={changed}
      />
      <DropConnectionDialog
        connection={dropTarget}
        onOpenChange={(open) => {
          if (!open) setDropTarget(null)
        }}
        onDropped={changed}
      />
    </div>
  )
}
