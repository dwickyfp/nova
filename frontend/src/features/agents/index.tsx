import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { Bot, Plus, Save, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { agentsApi, type Agent } from './api'

export function AgentsPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [draft, setDraft] = useState({ name: '', description: '' })

  const agentsQuery = useQuery({
    queryKey: ['agents', 'list'],
    queryFn: () => agentsApi.list(),
  })

  const createAgent = useMutation({
    mutationFn: () =>
      agentsApi.create({
        name: draft.name.trim(),
        description: draft.description.trim(),
        default_tools: ['load_skill', 'semantic_query', 'data_to_chart'],
        policy: 'auto_read_only',
      }),
    onSuccess: (agent) => {
      toast.success(`Agent "${agent.name}" created`)
      setCreateOpen(false)
      setDraft({ name: '', description: '' })
      queryClient.invalidateQueries({ queryKey: ['agents', 'list'] })
      navigate({ to: '/agents/$agentId', params: { agentId: agent.agent_id } })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const agents = useMemo(() => {
    const list = agentsQuery.data?.agents ?? []
    const term = search.trim().toLowerCase()
    if (!term) return list
    return list.filter(
      (a) =>
        a.name.toLowerCase().includes(term) ||
        a.description.toLowerCase().includes(term)
    )
  }, [agentsQuery.data, search])

  return (
    <>
      <Header fixed />
      <Main>
        <div className='mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between'>
          <div className='min-w-0'>
            <h1 className='text-2xl leading-8 font-normal'>Agent Studio</h1>
            <p className='mt-1 text-sm text-muted-foreground'>
              Build agents with instructions, tools, and a semantic model, then
              chat with them in Nova Studio.
            </p>
          </div>
          <div className='flex shrink-0 items-center gap-2'>
            <Button variant='outline' onClick={() => navigate({ to: '/agents/semantic' })}>
              <Sparkles className='size-4' />
              Semantic models
            </Button>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className='size-4' />
              Create agent
            </Button>
          </div>
        </div>

        <div className='mb-4 flex items-center gap-2'>
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder='Search agents'
            className='max-w-sm'
          />
        </div>

        <div className='min-h-0 min-w-0 flex-1'>
          {agentsQuery.isLoading ? (
            <div className='space-y-2'>
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className='h-12 w-full' />
              ))}
            </div>
          ) : agents.length === 0 ? (
            <EmptyState
              icon={Bot}
              title='No agents yet'
              description='Create your first agent to answer business questions from a semantic model.'
              action={
                <Button onClick={() => setCreateOpen(true)}>
                  <Plus className='size-4' />
                  Create agent
                </Button>
              }
            />
          ) : (
            <Table className='min-w-[36rem] table-fixed'>
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead className='w-40'>Semantic data</TableHead>
                  <TableHead className='w-28'>Updated</TableHead>
                  <TableHead className='w-28 text-right'>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {agents.map((agent) => (
                  <TableRow key={agent.agent_id}>
                    <TableCell>
                      <div className='flex flex-col'>
                        <span className='truncate font-medium' title={agent.name}>{agent.name}</span>
                        {agent.description ? (
                          <span className='truncate text-xs text-muted-foreground' title={agent.description}>
                            {agent.description}
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className='text-muted-foreground'>
                      {agent.semantic_model_ids.length > 0
                        ? `${agent.semantic_model_ids.length} model${
                            agent.semantic_model_ids.length === 1 ? '' : 's'
                          }`
                        : 'No semantic model'}
                    </TableCell>
                    <TableCell className='text-muted-foreground'>
                      {new Date(agent.updated_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell className='text-right'>
                      <Button asChild size='sm' variant='outline'>
                        <Link to='/agents/$agentId' params={{ agentId: agent.agent_id }}>
                          Configure
                        </Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </Main>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create agent</DialogTitle>
          </DialogHeader>
          <div className='space-y-4'>
            <div className='space-y-2'>
              <Label htmlFor='agent-name'>Agent name</Label>
              <Input
                id='agent-name'
                value={draft.name}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                placeholder='Revenue Analyst'
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='agent-desc'>Description</Label>
              <Input
                id='agent-desc'
                value={draft.description}
                onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
                placeholder='Answers sales and revenue questions'
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!draft.name.trim() || createAgent.isPending}
              onClick={() => createAgent.mutate()}
            >
              <Save className='size-4' />
              Create agent
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

export type { Agent }
