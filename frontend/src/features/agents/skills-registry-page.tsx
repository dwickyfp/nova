import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
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
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { skillsApi } from './api'

const SKILL_TEMPLATE = `---
name: my-skill
title: My Skill
summary: One line on when to use this skill.
triggers: keyword1, keyword2
---

# My Skill

Write the playbook here: what to do, the exact SQL shapes to use, and the
guardrails to respect. The model loads this when a task matches a trigger.
`

/**
 * Skill Registry. Two kinds in one place:
 *  - Builtin: playbooks packaged with Nova (read-only; they ship as files).
 *  - Yours: SKILL.md-compatible playbooks you add, available to your agents.
 */
export function SkillsRegistryPage() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState({ name: '', description: '', body: SKILL_TEMPLATE })

  const skillsQuery = useQuery({ queryKey: ['skills'], queryFn: () => skillsApi.list() })

  const create = useMutation({
    mutationFn: () =>
      skillsApi.create({
        name: draft.name.trim(),
        description: draft.description.trim(),
        body: draft.body,
      }),
    onSuccess: (skill) => {
      toast.success(`Skill "${skill.name}" created`)
      setOpen(false)
      setDraft({ name: '', description: '', body: SKILL_TEMPLATE })
      queryClient.invalidateQueries({ queryKey: ['skills'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const remove = useMutation({
    mutationFn: (id: string) => skillsApi.remove(id),
    onSuccess: () => {
      toast.success('Skill deleted')
      queryClient.invalidateQueries({ queryKey: ['skills'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const customs = skillsQuery.data?.skills ?? []

  return (
    <>
      <Header fixed />
      <Main>
        <div className='mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between'>
          <div className='min-w-0'>
            <h1 className='text-2xl font-semibold tracking-tight'>Skill Registry</h1>
            <p className='mt-1 text-sm text-muted-foreground'>
              Playbooks an agent loads before answering a task it covers. Add one
              as a SKILL.md document and any agent can pick it up.
            </p>
          </div>
          <Button onClick={() => setOpen(true)}>
            <Plus className='size-4' />
            Add skill
          </Button>
        </div>

        <ScrollArea className='min-h-0 flex-1'>
          <section>
            {skillsQuery.isLoading ? (
              <Skeleton className='h-32 w-full' />
            ) : customs.length === 0 ? (
              <EmptyState
                icon={BookOpen}
                title='No skills yet'
                description='Add a SKILL.md playbook to teach your agents a specific task.'
                action={
                  <Button onClick={() => setOpen(true)}>
                    <Plus className='size-4' />
                    Add skill
                  </Button>
                }
              />
            ) : (
              <div className='grid gap-3 sm:grid-cols-2'>
                {customs.map((skill) => (
                  <div
                    key={skill.skill_id}
                    className='flex items-start justify-between gap-3 rounded-lg border p-4'
                  >
                    <div className='min-w-0'>
                      <div className='flex items-center gap-2'>
                        <span className='font-mono text-sm font-medium'>{skill.name}</span>
                        <Badge variant='outline'>{skill.scope}</Badge>
                      </div>
                      {skill.description ? (
                        <p className='mt-1 line-clamp-2 text-sm text-muted-foreground'>
                          {skill.description}
                        </p>
                      ) : null}
                    </div>
                    <Button
                      size='icon'
                      variant='ghost'
                      onClick={() => remove.mutate(skill.skill_id)}
                      aria-label={`Delete ${skill.name}`}
                    >
                      <Trash2 className='size-4' />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </ScrollArea>
      </Main>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className='max-w-2xl'>
          <DialogHeader>
            <DialogTitle>Add skill</DialogTitle>
          </DialogHeader>
          <div className='space-y-4'>
            <div className='grid gap-4 sm:grid-cols-2'>
              <div className='space-y-2'>
                <Label htmlFor='skill-name'>Name</Label>
                <Input
                  id='skill-name'
                  value={draft.name}
                  onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                  placeholder='revenue-playbook'
                  className='font-mono'
                />
              </div>
              <div className='space-y-2'>
                <Label htmlFor='skill-desc'>Description</Label>
                <Input
                  id='skill-desc'
                  value={draft.description}
                  onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
                  placeholder='How to answer revenue questions'
                />
              </div>
            </div>
            <div className='space-y-2'>
              <Label htmlFor='skill-body'>SKILL.md</Label>
              <Textarea
                id='skill-body'
                value={draft.body}
                onChange={(e) => setDraft((d) => ({ ...d, body: e.target.value }))}
                className='min-h-72 font-mono text-xs'
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
              Add skill
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
