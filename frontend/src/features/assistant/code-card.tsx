import { useCallback, useState } from 'react'
import { Check, Copy, Loader2, Play } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import type { QueryResponse } from '@/features/workspaces/types'
import type { TurnContext } from './stream-client'

export type CodeCardStatus = 'idle' | 'running' | 'success' | 'error'

export type CodeCardProps = {
  code: string
  language: string
  /** Rendered highlighted HTML, or null when the language is not supported. */
  highlighted: string | null
  /** Database/schema/role to run against; omitted hides the Run control. */
  runContext?: TurnContext
  /** Whether this is a SQL card (the only kind that can be run). */
  runnable?: boolean
}

function useCopy() {
  const [copied, setCopied] = useState(false)
  const copy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }, [])
  return { copied, copy }
}

const STATUS_LABEL: Record<CodeCardStatus, string> = {
  idle: 'Not run',
  running: 'Running',
  success: 'Success',
  error: 'Failed',
}

/**
 * A fenced code block rendered as a card with a header. SQL cards get a Run
 * button (execute on the conversation's database/schema/role) and a status
 * badge that starts neutral and turns green on success or red on failure; every
 * card gets Copy. The status is per-card, so two SQL blocks in one answer each
 * report their own outcome.
 *
 * Running is explicit: it happens only on a click, and the statement still
 * passes through the backend's destructive guard. A native confirm on top of
 * the click adds a second prompt the user has already opted into.
 */
export function CodeCard({ code, language, highlighted, runContext, runnable }: CodeCardProps) {
  const { copied, copy } = useCopy()
  const [status, setStatus] = useState<CodeCardStatus>('idle')
  const [detail, setDetail] = useState<string | null>(null)

  const canRun = Boolean(runnable && runContext)

  const run = useCallback(async () => {
    if (!canRun || status === 'running') return

    setStatus('running')
    setDetail(null)
    try {
      const results = await api.post<QueryResponse[]>('/query/execute', {
        sql: code,
        database: runContext?.database ?? null,
        schema: runContext?.schema ?? null,
        role: runContext?.role ?? null,
        max_rows: 500,
        confirm_destructive: false,
      })
      const first = results?.[0]
      if (!first || !first.success) {
        setStatus('error')
        setDetail(first?.error ?? 'The statement failed.')
        return
      }
      setStatus('success')
      setDetail(summarize(first))
    } catch (error) {
      setStatus('error')
      setDetail(
        error instanceof Error ? error.message : 'The statement could not be run.'
      )
    }
  }, [canRun, code, runContext?.database, runContext?.role, runContext?.schema, status])

  return (
    <div className='my-2 overflow-hidden rounded-md border bg-surface-1'>
      <div className='flex items-center gap-1 border-b bg-surface-2 px-2 py-1'>
        <span className='flex-1 truncate font-mono text-[0.7rem] uppercase text-muted-foreground'>
          {language || 'text'}
        </span>

        <StatusBadge status={status} />

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type='button'
              size='icon'
              variant='ghost'
              className='size-7'
              aria-label='Copy code'
              onClick={() => void copy(code)}
            >
              {copied ? (
                <Check aria-hidden='true' className='size-3.5 text-success-strong' />
              ) : (
                <Copy aria-hidden='true' className='size-3.5' />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{copied ? 'Copied' : 'Copy'}</TooltipContent>
        </Tooltip>

        {canRun ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type='button'
                size='icon'
                variant='ghost'
                className='size-7'
                aria-label='Run statement'
                disabled={status === 'running'}
                onClick={() => void run()}
              >
                {status === 'running' ? (
                  <Loader2 aria-hidden='true' className='size-3.5 animate-spin' />
                ) : (
                  <Play aria-hidden='true' className='size-3.5' />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>Run</TooltipContent>
          </Tooltip>
        ) : null}
      </div>

      <pre className='overflow-x-auto p-2 text-xs'>
        {highlighted ? (
          <code
            className='hljs font-mono'
            // highlight.js output is generated from the model's text with a
            // fixed set of language grammars; it emits only span elements and
            // class names, never script, so the HTML is inert markup.
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        ) : (
          <code className='font-mono'>{code}</code>
        )}
      </pre>

      {detail ? (
        <p
          className={cn(
            'border-t px-2 py-1 text-xs',
            status === 'error' ? 'text-destructive' : 'text-muted-foreground'
          )}
        >
          {detail}
        </p>
      ) : null}
    </div>
  )
}

function StatusBadge({ status }: { status: CodeCardStatus }) {
  return (
    <span
      data-status={status}
      className={cn(
        'rounded-full px-2 py-0.5 text-[0.65rem] font-medium',
        status === 'idle' && 'bg-muted text-muted-foreground',
        status === 'running' && 'bg-info/15 text-info-strong',
        status === 'success' && 'bg-success/15 text-success-strong',
        status === 'error' && 'bg-destructive/15 text-destructive'
      )}
    >
      {STATUS_LABEL[status]}
    </span>
  )
}

function summarize(result: QueryResponse): string {
  const parts: string[] = []
  if (result.row_count) parts.push(`${result.row_count} row${result.row_count === 1 ? '' : 's'}`)
  if (result.affected_rows) parts.push(`${result.affected_rows} affected`)
  parts.push(`${Math.round(result.elapsed_ms)} ms`)
  return parts.join(' · ')
}
