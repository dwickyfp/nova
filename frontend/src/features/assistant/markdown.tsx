import { memo, useMemo } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import hljs from 'highlight.js/lib/core'
import sql from 'highlight.js/lib/languages/sql'
import json from 'highlight.js/lib/languages/json'
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import { cn } from '@/lib/utils'
import { CodeCard } from './code-card'
import type { TurnContext } from './stream-client'

/**
 * Markdown renderer for assistant answers. GFM is enabled so the tables and
 * task lists the model can emit render; a strict component map keeps every
 * element on Nova's semantic tokens instead of a global `prose` stylesheet,
 * which the design-system gate does not allow to carry raw palette classes.
 *
 * Only the languages the assistant actually produces are registered, so the
 * bundle stays small (highlight.js is imported per-language from `lib/core`).
 */
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('json', json)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)

/** Languages a Run button is offered for. */
const RUNNABLE_LANGUAGES = new Set(['sql', 'mysql'])

function CodeBlock({
  code,
  language,
  runContext,
}: {
  code: string
  language: string
  runContext?: TurnContext
}) {
  const normalized = language.toLowerCase()
  const html = useMemo(() => {
    if (language && hljs.getLanguage(normalized)) {
      try {
        return hljs.highlight(code, { language: normalized }).value
      } catch {
        return null
      }
    }
    return null
  }, [code, language, normalized])

  return (
    <CodeCard
      code={code}
      language={language}
      highlighted={html}
      runContext={runContext}
      runnable={RUNNABLE_LANGUAGES.has(normalized)}
    />
  )
}

const components: Components = {
  h1: ({ children }) => <h1 className='mt-3 mb-1 text-base font-semibold'>{children}</h1>,
  h2: ({ children }) => <h2 className='mt-3 mb-1 text-sm font-semibold'>{children}</h2>,
  h3: ({ children }) => <h3 className='mt-2 mb-1 text-sm font-semibold'>{children}</h3>,
  p: ({ children }) => <p className='my-1.5 leading-relaxed'>{children}</p>,
  ul: ({ children }) => <ul className='my-1.5 list-disc space-y-0.5 pl-5'>{children}</ul>,
  ol: ({ children }) => <ol className='my-1.5 list-decimal space-y-0.5 pl-5'>{children}</ol>,
  li: ({ children }) => <li className='leading-relaxed'>{children}</li>,
  a: ({ href, children }) => (
    <a
      href={href}
      target='_blank'
      rel='noreferrer noopener'
      className='text-info-strong underline underline-offset-2'
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className='my-2 border-l-2 border-border pl-3 text-muted-foreground'>
      {children}
    </blockquote>
  ),
  hr: () => <hr className='my-3 border-border' />,
  strong: ({ children }) => <strong className='font-semibold'>{children}</strong>,
  table: ({ children }) => (
    <div className='my-2 overflow-x-auto rounded-md border'>
      <table className='w-full border-collapse text-xs'>{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className='bg-surface-2'>{children}</thead>,
  th: ({ children }) => (
    <th className='border-b border-border px-2 py-1 text-left font-medium'>{children}</th>
  ),
  td: ({ children }) => <td className='border-b border-border px-2 py-1'>{children}</td>,
  pre: ({ children }) => <>{children}</>,
}

export const Markdown = memo(function Markdown({
  children,
  className,
  runContext,
}: {
  children: string
  className?: string
  /** Context a SQL code card's Run button executes against. */
  runContext?: TurnContext
}) {
  // The `code` override closes over the run context so a fenced SQL block can
  // offer Run. Only that one entry differs, so the rest of the map is reused.
  const componentsWithRun = useMemo<Components>(
    () => ({
      ...components,
      code: ({ className, children, ...rest }) => {
        const match = /language-(\w+)/.exec(className ?? '')
        const text = String(children).replace(/\n$/, '')
        if (match || text.includes('\n')) {
          return (
            <CodeBlock code={text} language={match?.[1] ?? ''} runContext={runContext} />
          )
        }
        return (
          <code
            className='rounded bg-surface-1 px-1 py-0.5 font-mono text-[0.85em]'
            {...rest}
          >
            {children}
          </code>
        )
      },
    }),
    [runContext]
  )

  return (
    <div className={cn('break-words', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={componentsWithRun}>
        {children}
      </ReactMarkdown>
    </div>
  )
})
