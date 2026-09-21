import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Pencil, Plus, Save, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  agentsApi,
  customToolsApi,
  mcpApi,
  skillsApi,
  toolsApi,
  type AgentCreateInput,
  type Agent,
  type CustomTool,
} from '@/features/agents/api'
import {
  CustomToolEditorDialog,
  type CustomToolDraft,
} from '@/features/agents/agent-detail/custom-tool-editor'

/** Tools a user agent may bundle; authoring tools are Nove-only. */
const BUNDLEABLE = new Set([
  'load_skill',
  'query_execute',
  'semantic_query',
  'semantic_search',
  'data_to_chart',
])

/**
 * Agent Configuration, split into the same sub-tabs as the detail surface:
 * General, Instructions, Tools, Skills, MCP. Each writes the same agent record;
 * the sub-tab is only a view.
 */
export function AgentConfigurationTab({
  agent,
  onSaved,
}: {
  agent: Agent
  onSaved: () => void
}) {
  const [draft, setDraft] = useState<AgentCreateInput>({ ...agent })
  useEffect(() => {
    setDraft({ ...agent })
  }, [agent])

  const save = useMutation({
    mutationFn: (body: AgentCreateInput) => agentsApi.update(agent.agent_id, body),
    onSuccess: () => {
      toast.success('Agent saved')
      onSaved()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const set = <K extends keyof AgentCreateInput>(key: K, value: AgentCreateInput[K]) =>
    setDraft((prev) => ({ ...prev, [key]: value }))

  return (
    <div className='space-y-4'>
      <div className='flex items-center justify-end'>
        <Button disabled={save.isPending} onClick={() => save.mutate(draft)}>
          <Save className='size-4' />
          Save changes
        </Button>
      </div>

      <Tabs defaultValue='general'>
        <TabsList>
          <TabsTrigger value='general'>General</TabsTrigger>
          <TabsTrigger value='instructions'>Instructions</TabsTrigger>
          <TabsTrigger value='tools'>Tools</TabsTrigger>
          <TabsTrigger value='skills'>Skills</TabsTrigger>
          <TabsTrigger value='mcp'>MCP</TabsTrigger>
        </TabsList>

        <TabsContent value='general' className='mt-6 max-w-2xl space-y-5'>
          <div className='space-y-1.5'>
            <Label htmlFor='c-name'>Display name</Label>
            <Input
              id='c-name'
              value={draft.name ?? ''}
              onChange={(e) => set('name', e.target.value)}
              placeholder='What users will see in the agent picker'
            />
          </div>
          <div className='space-y-1.5'>
            <Label htmlFor='c-desc'>Description</Label>
            <Textarea
              id='c-desc'
              value={draft.description ?? ''}
              onChange={(e) => set('description', e.target.value)}
              placeholder='One line on what this agent is for'
              className='min-h-24'
            />
            <p className='text-xs text-muted-foreground'>
              Shown to users in Nova Studio. It does not affect how the agent
              answers; the Instructions tab does that.
            </p>
          </div>
        </TabsContent>

        <TabsContent value='instructions' className='mt-6 max-w-3xl space-y-4'>
          <div className='space-y-2'>
            <Label>Model</Label>
            <Input
              value={draft.model_name ?? 'Provider default'}
              disabled
              className='max-w-xs'
            />
            <p className='text-xs text-muted-foreground'>
              Inherits the provider configured under AI Providers.
            </p>
          </div>
          <div className='space-y-2'>
            <Label htmlFor='c-orch'>Orchestration instructions</Label>
            <Textarea
              id='c-orch'
              value={draft.instructions_orchestration ?? ''}
              onChange={(e) => set('instructions_orchestration', e.target.value)}
              placeholder='Define how the agent reasons through tasks and chooses tools.'
              className='min-h-48'
            />
          </div>
          <div className='space-y-2'>
            <Label htmlFor='c-resp'>Response instructions</Label>
            <Textarea
              id='c-resp'
              value={draft.instructions_response ?? ''}
              onChange={(e) => set('instructions_response', e.target.value)}
              placeholder='Set rules for how the agent should sound and respond to users.'
              className='min-h-32'
            />
          </div>
        </TabsContent>

        <TabsContent value='tools' className='mt-6'>
          <ToolsConfig draft={draft} set={set} />
        </TabsContent>

        <TabsContent value='skills' className='mt-6'>
          <SkillsConfig draft={draft} set={set} />
        </TabsContent>

        <TabsContent value='mcp' className='mt-6 max-w-2xl'>
          <McpConfig />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function ToolsConfig({
  draft,
  set,
}: {
  draft: AgentCreateInput
  set: <K extends keyof AgentCreateInput>(key: K, value: AgentCreateInput[K]) => void
}) {
  const toolsQuery = useQuery({ queryKey: ['tools'], queryFn: () => toolsApi.list() })
  const modelsQuery = useQuery({
    queryKey: ['agents', 'semantic-models'],
    queryFn: () => agentsApi.listSemanticModels(),
  })

  const toggle = (name: string) => {
    const current = new Set(draft.default_tools ?? [])
    if (current.has(name)) current.delete(name)
    else current.add(name)
    set('default_tools', Array.from(current) as AgentCreateInput['default_tools'])
  }

  const bundleable =
    toolsQuery.data?.tools.filter((t) => BUNDLEABLE.has(t.name)) ?? []

  return (
    <div className='max-w-3xl space-y-6'>
      <p className='text-sm text-muted-foreground'>
        Choose the tools this agent can use during conversations.
      </p>

      <section className='space-y-2'>
        <h3 className='text-sm font-medium'>Builtin tools</h3>
        <div className='grid gap-2 sm:grid-cols-2'>
          {bundleable.map((tool) => (
            <label
              key={tool.name}
              className='flex items-start gap-2 rounded-xl border p-3 text-sm'
            >
              <Checkbox
                checked={(draft.default_tools ?? []).includes(tool.name)}
                onCheckedChange={() => toggle(tool.name)}
              />
              <span className='min-w-0'>
                <span className='block font-mono text-xs font-medium'>{tool.name}</span>
                <span className='block text-xs text-muted-foreground'>
                  {tool.description}
                </span>
              </span>
            </label>
          ))}
        </div>
      </section>

      <section className='space-y-3'>
        <div className='flex items-center justify-between'>
          <div>
            <h3 className='text-sm font-medium'>Query structured data</h3>
            <p className='text-xs text-muted-foreground'>
              Add semantic views to query data using natural language. An agent may
              use more than one.
            </p>
          </div>
        </div>

        <AddSemanticView
          models={modelsQuery.data?.models ?? []}
          selected={draft.semantic_model_ids ?? []}
          onChange={(ids) => set('semantic_model_ids', ids)}
        />

        {(draft.semantic_model_ids ?? []).length === 0 ? (
          <p className='text-xs text-muted-foreground'>No semantic views added.</p>
        ) : (
          <div className='space-y-2'>
            {(draft.semantic_model_ids ?? []).map((id) => {
              const model = modelsQuery.data?.models.find(
                (m) => m.semantic_model_id === id
              )
              if (!model) return null
              const datasets = Array.isArray(model.definition.datasets)
                ? (model.definition.datasets as unknown[]).length
                : 0
              return (
                <div
                  key={id}
                  className='flex items-start justify-between gap-3 rounded-2xl border p-4'
                >
                  <div className='min-w-0'>
                    <div className='flex items-center gap-2'>
                      <span className='font-medium'>{model.name}</span>
                      <Badge variant='outline'>Ossie {model.ossie_version}</Badge>
                    </div>
                    {model.description ? (
                      <p className='mt-1 line-clamp-2 text-sm text-muted-foreground'>
                        {model.description}
                      </p>
                    ) : null}
                    <p className='mt-1 text-xs text-muted-foreground'>
                      {model.database_name ?? '—'} · {datasets} datasets
                    </p>
                  </div>
                  <Button
                    size='icon'
                    variant='ghost'
                    aria-label={`Remove ${model.name}`}
                    onClick={() =>
                      set(
                        'semantic_model_ids',
                        (draft.semantic_model_ids ?? []).filter((x) => x !== id)
                      )
                    }
                  >
                    <Trash2 className='size-4' />
                  </Button>
                </div>
              )
            })}
          </div>
        )}
      </section>

      <CustomToolsSection draft={draft} set={set} />
    </div>
  )
}

function AddSemanticView({
  models,
  selected,
  onChange,
}: {
  models: { semantic_model_id: string; name: string }[]
  selected: string[]
  onChange: (ids: string[]) => void
}) {
  const [pick, setPick] = useState<string>('')
  const available = models.filter((m) => !selected.includes(m.semantic_model_id))

  return (
    <div className='flex items-center gap-2'>
      <Select value={pick || undefined} onValueChange={setPick}>
        <SelectTrigger className='max-w-xs'>
          <SelectValue placeholder='Select a semantic view' />
        </SelectTrigger>
        <SelectContent>
          {available.length === 0 ? (
            <SelectItem value='__none__' disabled>
              No more models
            </SelectItem>
          ) : (
            available.map((model) => (
              <SelectItem key={model.semantic_model_id} value={model.semantic_model_id}>
                {model.name}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
      <Button
        variant='outline'
        disabled={!pick || pick === '__none__'}
        onClick={() => {
          onChange([...selected, pick])
          setPick('')
        }}
      >
        <Plus className='size-4' />
        Add
      </Button>
    </div>
  )
}

function CustomToolsSection({
  draft,
  set,
}: {
  draft: AgentCreateInput
  set: <K extends keyof AgentCreateInput>(key: K, value: AgentCreateInput[K]) => void
}) {
  const queryClient = useQueryClient()
  const toolsQuery = useQuery({
    queryKey: ['custom-tools'],
    queryFn: () => customToolsApi.list(),
  })
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<CustomTool | null>(null)

  const save = useMutation({
    mutationFn: (d: CustomToolDraft) => {
      const payload = {
        name: d.name.trim(),
        description: d.description.trim(),
        kind: 'procedure' as const,
        database_name: d.database_name.trim() || null,
        function_name: null,
        definition: {
          parameters: d.parameters,
          statements: d.statements.filter((s) => s.trim()),
          output_mode: d.output_mode,
        },
      }
      return editing
        ? customToolsApi.update(editing.tool_id, payload)
        : customToolsApi.create(payload)
    },
    onSuccess: () => {
      toast.success(editing ? 'Custom tool updated' : 'Custom tool created')
      setOpen(false)
      setEditing(null)
      queryClient.invalidateQueries({ queryKey: ['custom-tools'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const remove = useMutation({
    mutationFn: (id: string) => customToolsApi.remove(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['custom-tools'] }),
  })

  const toggle = (name: string) => {
    const key = `custom:${name}`
    const current = new Set(draft.default_tools ?? [])
    if (current.has(key)) current.delete(key)
    else current.add(key)
    set('default_tools', Array.from(current) as AgentCreateInput['default_tools'])
  }

  const tools = toolsQuery.data?.tools ?? []

  return (
    <section className='space-y-2'>
      <div className='flex items-center justify-between'>
        <h3 className='text-sm font-medium'>Custom tools</h3>
        <Button size='sm' variant='outline' onClick={() => setOpen(true)}>
          <Plus className='size-4' />
          Add
        </Button>
      </div>
      {tools.length === 0 ? (
        <p className='text-xs text-muted-foreground'>
          Add a StarRocks function or a Nova SQL procedure as a tool.
        </p>
      ) : (
        <div className='space-y-2'>
          {tools.map((tool) => (
            <label
              key={tool.tool_id}
              className='flex items-start justify-between gap-2 rounded-xl border p-3 text-sm'
            >
              <span className='flex items-start gap-2'>
                <Checkbox
                  checked={(draft.default_tools ?? []).includes(`custom:${tool.name}`)}
                  onCheckedChange={() => toggle(tool.name)}
                />
                <span className='min-w-0'>
                  <span className='flex items-center gap-2'>
                    <span className='font-mono text-xs font-medium'>{tool.name}</span>
                    <Badge variant='outline'>{tool.kind}</Badge>
                  </span>
                  <span className='block text-xs text-muted-foreground'>
                    {tool.description}
                  </span>
                </span>
              </span>
              <span className='flex items-center gap-1'>
                <Button
                  size='icon'
                  variant='ghost'
                  onClick={() => {
                    setEditing(tool)
                    setOpen(true)
                  }}
                  aria-label={`Edit ${tool.name}`}
                >
                  <Pencil className='size-4' />
                </Button>
                <Button
                  size='icon'
                  variant='ghost'
                  onClick={() => remove.mutate(tool.tool_id)}
                  aria-label={`Delete ${tool.name}`}
                >
                  <Trash2 className='size-4' />
                </Button>
              </span>
            </label>
          ))}
        </div>
      )}

      <CustomToolEditorDialog
        open={open}
        onOpenChange={(v) => {
          setOpen(v)
          if (!v) setEditing(null)
        }}
        initial={editing}
        submitting={save.isPending}
        onSubmit={(d) => save.mutate(d)}
      />
    </section>
  )
}

function SkillsConfig({
  draft,
  set,
}: {
  draft: AgentCreateInput
  set: <K extends keyof AgentCreateInput>(key: K, value: AgentCreateInput[K]) => void
}) {
  const skillsQuery = useQuery({ queryKey: ['skills'], queryFn: () => skillsApi.list() })
  const skills = skillsQuery.data?.skills ?? []

  const toggle = (name: string) => {
    const current = new Set(draft.default_skills ?? [])
    if (current.has(name)) current.delete(name)
    else current.add(name)
    set('default_skills', Array.from(current))
  }

  return (
    <div className='max-w-3xl space-y-3'>
      <div className='flex items-center justify-between'>
        <p className='text-sm text-muted-foreground'>
          Playbooks this agent can load before answering.
        </p>
        <Button asChild size='sm' variant='ghost'>
          <Link to='/agents/skills'>Manage in Skill Registry</Link>
        </Button>
      </div>
      {skillsQuery.isLoading ? (
        <Skeleton className='h-24 w-full' />
      ) : skills.length === 0 ? (
        <p className='rounded-xl border border-dashed px-4 py-6 text-center text-sm text-muted-foreground'>
          No skills yet. Add one in the Skill Registry.
        </p>
      ) : (
        <div className='grid gap-2 sm:grid-cols-2'>
          {skills.map((skill) => (
            <label
              key={skill.skill_id}
              className='flex items-start gap-2 rounded-xl border p-3 text-sm'
            >
              <Checkbox
                checked={(draft.default_skills ?? []).includes(skill.name)}
                onCheckedChange={() => toggle(skill.name)}
              />
              <span className='min-w-0'>
                <span className='block font-mono text-xs'>{skill.name}</span>
                {skill.description ? (
                  <span className='block text-xs text-muted-foreground'>
                    {skill.description}
                  </span>
                ) : null}
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

function McpConfig() {
  const serversQuery = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: () => mcpApi.list(),
  })
  const servers = serversQuery.data?.servers ?? []

  return (
    <div className='space-y-3'>
      <div className='flex items-center justify-between'>
        <p className='text-sm text-muted-foreground'>
          MCP servers you have connected. Tools they expose appear under Tools.
        </p>
        <Button asChild size='sm' variant='ghost'>
          <Link to='/agents/tools'>Manage MCP servers</Link>
        </Button>
      </div>
      {serversQuery.isLoading ? (
        <Skeleton className='h-24 w-full' />
      ) : servers.length === 0 ? (
        <div className='rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground'>
          No MCP servers connected.
        </div>
      ) : (
        <div className='divide-y rounded-xl border'>
          {servers.map((server) => (
            <div
              key={server.server_id}
              className='flex items-center justify-between px-4 py-3'
            >
              <div className='flex items-center gap-2'>
                <span className='text-sm font-medium'>{server.name}</span>
                <Badge variant='outline'>{server.transport}</Badge>
              </div>
              {server.last_status ? (
                <Badge
                  variant={server.last_status === 'connected' ? 'secondary' : 'destructive'}
                >
                  {server.last_status}
                </Badge>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
