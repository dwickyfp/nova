import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plug, Plus, RefreshCw, Trash2, Wrench } from 'lucide-react'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Badge } from '@/components/ui/badge'
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
import { mcpApi, toolsApi } from './api'

/**
 * Tools Registry. Two sources in one place:
 *  - Builtin: Nova's own tools, seeded by the backend. Disable to hide; they
 *    cannot be deleted because they are code.
 *  - MCP: the admin-managed internal connector catalog.
 */
export function ToolsRegistryPage() {
  const [tab, setTab] = useState('tools')

  return (
    <>
      <Header fixed />
      <Main>
        <div className='mb-6'>
          <h1 className='text-2xl leading-8 font-normal'>Tools Registry</h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            Nova's builtin tools and the internal MCP connector catalog.
          </p>
        </div>

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
  const toolsQuery = useQuery({ queryKey: ['tools'], queryFn: () => toolsApi.list() })

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toolsApi.toggle(id, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tools'] }),
    onError: (e: Error) => toast.error(e.message),
  })

  if (toolsQuery.isLoading) return <Skeleton className='mt-4 h-64 w-full' />

  const tools = toolsQuery.data?.tools ?? []
  if (tools.length === 0) {
    return (
      <EmptyState
        icon={Wrench}
        title='No tools registered'
        description='Builtin tools appear here automatically.'
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
              <Badge variant={tool.source === 'builtin' ? 'secondary' : 'outline'}>
                {tool.source === 'builtin' ? 'builtin' : 'MCP'}
              </Badge>
            </div>
            <p className='mt-1 text-sm text-muted-foreground'>{tool.description}</p>
          </div>
          <div className='flex shrink-0 items-center gap-2'>
            <Switch
              checked={tool.is_enabled}
              onCheckedChange={(v) => toggle.mutate({ id: tool.tool_id, enabled: v })}
              aria-label={`Enable ${tool.name}`}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

function McpServers() {
  const queryClient = useQueryClient()
  const canManage = useAuthStore((state) => state.auth.user?.roles.includes('ACCOUNTADMIN') ?? false)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState({
    name: '',
    description: '',
    transport: 'http' as 'http' | 'sse' | 'stdio',
    endpoint: '',
  })

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
        endpoint: draft.endpoint.trim() || null,
      }),
    onSuccess: () => {
      toast.success('MCP server added')
      setOpen(false)
      setDraft({ name: '', description: '', transport: 'http', endpoint: '' })
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
      </div> : <p className='mb-4 text-sm text-muted-foreground'>MCP connectors are managed by your Nova administrator.</p>}

      {serversQuery.isLoading ? (
        <Skeleton className='h-40 w-full' />
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
                    <Badge
                      variant={server.last_status === 'connected' ? 'secondary' : 'destructive'}
                    >
                      {server.last_status}
                    </Badge>
                  ) : null}
                </div>
                <p className='mt-1 truncate text-sm text-muted-foreground'>
                  {canManage ? server.endpoint || server.command || server.description : server.description}
                </p>
              </div>
              {canManage ? <div className='flex shrink-0 items-center gap-1'>
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
            <div className='space-y-2'>
              <Label htmlFor='mcp-endpoint'>Endpoint URL</Label>
              <Input
                id='mcp-endpoint'
                value={draft.endpoint}
                onChange={(e) => setDraft((d) => ({ ...d, endpoint: e.target.value }))}
                placeholder='https://tools.example.com/mcp'
                disabled={draft.transport === 'stdio'}
              />
            </div>
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
              disabled={!draft.name.trim() || create.isPending}
              onClick={() => create.mutate()}
            >
              Add server
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
