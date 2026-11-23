import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plug, Plus, RefreshCw, Trash2, Wrench } from 'lucide-react'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { PageHeader } from '@/components/ui/page-header'
import { Badge } from '@/components/ui/badge'
import { StateBadge } from '@/components/ui/status-badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { mcpApi, toolsApi, type McpServer, type Tool } from './api'

export function ToolsRegistryPage() {
  const [tab, setTab] = useState('tools')

  return (
    <>
      <Header fixed />
      <Main scroll>
        <PageHeader
          className='mb-6'
          title='Tools Registry'
          description='Tools agents get from connected MCP servers, and the servers that provide them'
        />

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value='tools' className='gap-1.5'>
              <Wrench className='size-3.5' />
              Tools
            </TabsTrigger>
            <TabsTrigger value='mcp' className='gap-1.5'>
              <Plug className='size-3.5' />
              MCP servers
            </TabsTrigger>
          </TabsList>
          <TabsContent value='tools'>
            <ToolsList />
          </TabsContent>
          <TabsContent value='mcp'>
            <McpServers />
          </TabsContent>
        </Tabs>
      </Main>
    </>
  )
}

function ToolsList() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.auth.user)
  const toolsQuery = useQuery({ queryKey: ['tools'], queryFn: () => toolsApi.list() })

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toolsApi.toggle(id, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tools'] }),
    onError: (e: Error) => toast.error(e.message),
  })

  if (toolsQuery.isLoading) return <Skeleton className='mt-4 h-64 w-full' />
  if (toolsQuery.isError) return (
    <EmptyState variant='error' title='Could not load tools' description={toolsQuery.error.message}
      action={<Button variant='outline' onClick={() => toolsQuery.refetch()}>Retry</Button>} />
  )

  const tools = (toolsQuery.data?.tools ?? []).filter(
    (tool) => tool.source !== 'builtin'
  )
  if (tools.length === 0) {
    return (
      <EmptyState
        icon={Wrench}
        title='No tools registered'
        description='Connect an MCP server and discover its tools to add them here'
      />
    )
  }

  return (
    <div className='mt-4 space-y-2'>
      {tools.map((tool) => (
        <div
          key={tool.tool_id}
          className='flex items-start justify-between gap-4 rounded-lg border p-4'
        >
          <div className='min-w-0'>
            <div className='flex items-center gap-2'>
              <span className='font-mono text-sm font-medium'>{tool.name}</span>
              <Badge variant='outline'>
                {tool.source.startsWith('mcp:') ? 'MCP' : tool.source}
              </Badge>
            </div>
            <p className='mt-1 text-sm text-muted-foreground'>{tool.description}</p>
            {tool.source.startsWith('mcp:') ? <ServerHints tool={tool} /> : null}
          </div>
          <div className='flex shrink-0 items-center gap-2'>
            <Switch
              checked={tool.is_enabled}
              disabled={toggle.isPending || (tool.owner_name !== user?.username && user?.activeRole !== 'ACCOUNTADMIN')}
              onCheckedChange={(v) => toggle.mutate({ id: tool.tool_id, enabled: v })}
              aria-label={`Enable ${tool.name}`}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * What the MCP server says about a tool. The hints are the server's own,
 * unverified claims: shown so an administrator can decide whether to keep the
 * tool on. They do not change how a call runs.
 */
function ServerHints({ tool }: { tool: Tool }) {
  const hints = tool.annotations ?? {}
  let label: string
  if (hints.destructiveHint === true) label = 'May delete or overwrite data'
  else if (hints.readOnlyHint === true) label = 'Only reads'
  else if (hints.readOnlyHint === false) label = 'Changes data'
  else label = 'Not described'
  return (
    <p className='mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground'>
      <span>The server says:</span>
      <Badge variant={hints.destructiveHint === true ? 'destructive' : 'outline'}>{label}</Badge>
    </p>
  )
}

type HeaderRow = { name: string; value: string }

function headerMap(rows: HeaderRow[]): Record<string, string> {
  return Object.fromEntries(
    rows.filter((row) => row.name.trim()).map((row) => [row.name.trim(), row.value]),
  )
}

/** Write-only header entry. Saved values are never read back. */
function AuthHeaderFields({
  rows,
  onChange,
}: {
  rows: HeaderRow[]
  onChange: (rows: HeaderRow[]) => void
}) {
  return (
    <div className='space-y-2'>
      {rows.map((row, index) => (
        <div key={index} className='flex gap-2'>
          <Input
            aria-label={`Header ${index + 1} name`}
            placeholder='Authorization'
            value={row.name}
            onChange={(e) =>
              onChange(rows.map((r, i) => (i === index ? { ...r, name: e.target.value } : r)))
            }
            className='w-2/5'
          />
          <Input
            aria-label={`Header ${index + 1} value`}
            type='password'
            autoComplete='off'
            placeholder='Value'
            value={row.value}
            onChange={(e) =>
              onChange(rows.map((r, i) => (i === index ? { ...r, value: e.target.value } : r)))
            }
          />
          <Button
            size='icon'
            variant='ghost'
            aria-label={`Remove header ${index + 1}`}
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
          >
            <Trash2 className='size-4' />
          </Button>
        </div>
      ))}
      {rows.length < 8 ? (
        <Button
          size='sm'
          variant='outline'
          onClick={() => onChange([...rows, { name: '', value: '' }])}
        >
          <Plus className='size-3.5' />
          Add header
        </Button>
      ) : null}
    </div>
  )
}

function McpServers() {
  const queryClient = useQueryClient()
  const canManage = useAuthStore((state) => state.auth.user?.activeRole === 'ACCOUNTADMIN')
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState({
    name: '',
    description: '',
    transport: 'http' as 'http' | 'sse' | 'stdio',
    endpoint: '',
    command: '',
  })
  const [headers, setHeaders] = useState<HeaderRow[]>([])
  const [editing, setEditing] = useState<McpServer | null>(null)
  const [replacement, setReplacement] = useState<HeaderRow[]>([])

  const serversQuery = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: () => mcpApi.list(),
  })

  const create = useMutation({
    mutationFn: () =>
      mcpApi.create({
        name: draft.name.trim(),
        description: draft.description.trim(),
        transport: draft.transport,
        ...(draft.transport === 'stdio'
          ? { endpoint: null, command: draft.command.trim() }
          : { endpoint: draft.endpoint.trim() || null }),
        ...(draft.transport !== 'stdio' && Object.keys(headerMap(headers)).length
          ? { auth_headers: headerMap(headers) }
          : {}),
      }),
    onSuccess: () => {
      toast.success('MCP server added')
      setOpen(false)
      setDraft({ name: '', description: '', transport: 'http', endpoint: '', command: '' })
      setHeaders([])
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const discover = useMutation({
    mutationFn: (id: string) => mcpApi.discover(id),
    onSuccess: (result) => {
      if (result.ok) {
        toast.success(`Connected: ${result.tools_discovered} tool(s) discovered`)
        queryClient.invalidateQueries({ queryKey: ['tools'] })
      } else {
        toast.error(result.error || 'Discovery failed')
      }
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const saveHeaders = useMutation({
    mutationFn: ({ id, values }: { id: string; values: Record<string, string> }) =>
      mcpApi.update(id, { auth_headers: values }),
    onSuccess: (server) => {
      toast.success(
        server.auth_header_names?.length ? 'Auth headers saved' : 'Auth headers removed',
      )
      setEditing(null)
      setReplacement([])
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const remove = useMutation({
    mutationFn: (id: string) => mcpApi.remove(id),
    onSuccess: () => {
      toast.success('Server removed')
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
      queryClient.invalidateQueries({ queryKey: ['tools'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const servers = serversQuery.data?.servers ?? []

  return (
    <div className='mt-4'>
      {canManage ? <div className='mb-4 flex justify-end'>
        <Button onClick={() => setOpen(true)}>
          <Plus className='size-4' />
          Add MCP server
        </Button>
      </div> : <p className='mb-4 text-sm text-muted-foreground'>Managing MCP connectors requires the active ACCOUNTADMIN role.</p>}

      {serversQuery.isLoading ? (
        <Skeleton className='h-40 w-full' />
      ) : serversQuery.isError ? (
        <EmptyState variant='error' title='Could not load MCP servers' description={serversQuery.error.message}
          action={<Button variant='outline' onClick={() => serversQuery.refetch()}>Retry</Button>} />
      ) : servers.length === 0 ? (
        <EmptyState
          icon={Plug}
          title='No MCP servers'
          description='Internal connectors appear here after an administrator adds them.'
          action={
            canManage ? <Button onClick={() => setOpen(true)}>
              <Plus className='size-4' />
              Add MCP server
            </Button> : undefined
          }
        />
      ) : (
        <div className='space-y-2'>
          {servers.map((server) => (
            <div
              key={server.server_id}
              className='flex items-start justify-between gap-4 rounded-lg border p-4'
            >
              <div className='min-w-0'>
                <div className='flex items-center gap-2'>
                  <span className='font-medium'>{server.name}</span>
                  <Badge variant='outline'>{server.transport}</Badge>
                  {server.last_status ? (
                    <StateBadge status={server.last_status} />
                  ) : null}
                </div>
                <p className='mt-1 truncate text-sm text-muted-foreground'>
                  {canManage ? server.endpoint || server.command || server.description : server.description}
                </p>
                {canManage && server.auth_header_names?.length ? (
                  <p className='mt-1 text-xs text-muted-foreground'>
                    Auth headers: {server.auth_header_names.join(', ')} (values hidden)
                  </p>
                ) : null}
              </div>
              {canManage ? <div className='flex shrink-0 items-center gap-1'>
                {server.transport !== 'stdio' ? (
                  <Button
                    size='sm'
                    variant='ghost'
                    onClick={() => {
                      setEditing(server)
                      setReplacement([{ name: '', value: '' }])
                    }}
                  >
                    Auth headers
                  </Button>
                ) : null}
                <Button
                  size='sm'
                  variant='outline'
                  disabled={discover.isPending}
                  onClick={() => discover.mutate(server.server_id)}
                >
                  <RefreshCw className='size-3.5' />
                  Discover
                </Button>
                <Button
                  size='icon'
                  variant='ghost'
                  onClick={() => remove.mutate(server.server_id)}
                  aria-label={`Remove ${server.name}`}
                >
                  <Trash2 className='size-4' />
                </Button>
              </div> : null}
            </div>
          ))}
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add MCP server</DialogTitle>
          </DialogHeader>
          <div className='space-y-4'>
            <div className='space-y-2'>
              <Label htmlFor='mcp-name'>Name</Label>
              <Input
                id='mcp-name'
                value={draft.name}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                placeholder='Internal tools'
              />
            </div>
            <div className='space-y-2'>
              <Label>Transport</Label>
              <Select
                value={draft.transport}
                onValueChange={(v) =>
                  setDraft((d) => ({ ...d, transport: v as typeof draft.transport }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value='http'>HTTP (Streamable)</SelectItem>
                  <SelectItem value='sse'>SSE (legacy)</SelectItem>
                  <SelectItem value='stdio'>stdio (not executed)</SelectItem>
                </SelectContent>
              </Select>
              {draft.transport === 'stdio' ? (
                <p className='text-xs text-warning-strong'>
                  stdio servers are stored for reference only. Nova does not run local
                  commands; expose the server over HTTP to discover its tools.
                </p>
              ) : null}
            </div>
            {draft.transport === 'stdio' ? (
              <div className='space-y-2'>
                <Label htmlFor='mcp-command'>Command</Label>
                <Input
                  id='mcp-command'
                  value={draft.command}
                  onChange={(e) => setDraft((d) => ({ ...d, command: e.target.value }))}
                  placeholder='npx -y @example/mcp-server'
                />
              </div>
            ) : (
              <div className='space-y-2'>
                <Label htmlFor='mcp-endpoint'>Endpoint URL</Label>
                <Input
                  id='mcp-endpoint'
                  value={draft.endpoint}
                  onChange={(e) => setDraft((d) => ({ ...d, endpoint: e.target.value }))}
                  placeholder='https://tools.example.com/mcp'
                />
              </div>
            )}
            {draft.transport !== 'stdio' ? (
              <div className='space-y-2'>
                <Label>Auth headers (optional)</Label>
                <p className='text-xs text-muted-foreground'>
                  Sent only to this endpoint. Values are stored encrypted and never shown again.
                </p>
                <AuthHeaderFields rows={headers} onChange={setHeaders} />
              </div>
            ) : null}
            <div className='space-y-2'>
              <Label htmlFor='mcp-desc'>Description</Label>
              <Input
                id='mcp-desc'
                value={draft.description}
                onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={
                !draft.name.trim() ||
                !(draft.transport === 'stdio' ? draft.command.trim() : draft.endpoint.trim()) ||
                create.isPending
              }
              onClick={() => create.mutate()}
            >
              Add server
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={editing !== null}
        onOpenChange={(value) => {
          if (!value) setEditing(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Auth headers for {editing?.name}</DialogTitle>
          </DialogHeader>
          <div className='space-y-3'>
            <p className='text-sm text-muted-foreground'>
              {editing?.auth_header_names?.length
                ? `Saved: ${editing.auth_header_names.join(', ')}. Saved values are never shown. Saving replaces every saved header.`
                : 'No headers are saved. Values are stored encrypted and sent only to this endpoint.'}
            </p>
            <AuthHeaderFields rows={replacement} onChange={setReplacement} />
          </div>
          <DialogFooter>
            {editing?.auth_header_names?.length ? (
              <Button
                variant='outline'
                disabled={saveHeaders.isPending}
                onClick={() => editing && saveHeaders.mutate({ id: editing.server_id, values: {} })}
              >
                Remove all headers
              </Button>
            ) : null}
            <Button
              disabled={saveHeaders.isPending || Object.keys(headerMap(replacement)).length === 0}
              onClick={() =>
                editing &&
                saveHeaders.mutate({ id: editing.server_id, values: headerMap(replacement) })
              }
            >
              Save headers
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
