import { memo, useCallback, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link } from '@tanstack/react-router'
import hljs from 'highlight.js/lib/core'
import sql from 'highlight.js/lib/languages/sql'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import { Check, Copy, Info, Lightbulb, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import { docsLink, parseDocsHref, slugify } from './content'

hljs.registerLanguage('sql', sql)
hljs.registerLanguage('python', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('yaml', yaml)

function textOf(node: ReactNode): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (typeof node === 'object' && 'props' in node) {
    return textOf((node.props as { children?: ReactNode }).children)
  }
  return ''
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const [copied, setCopied] = useState(false)
  const normalized = language.toLowerCase()
  const html = useMemo(() => {
    if (!normalized || !hljs.getLanguage(normalized)) return null
    try {
      return hljs.highlight(code, { language: normalized }).value
    } catch {
      return null
    }
  }, [code, normalized])

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }, [code])

  return (
    <div className='group/code my-4 overflow-hidden rounded-lg border border-surface-border bg-surface-1'>
      <div className='flex h-9 items-center justify-between border-b border-surface-border px-3'>
        <span className='text-xs text-muted-foreground'>
          {normalized ? normalized.toUpperCase() : 'Text'}
        </span>
        <button
          type='button'
          onClick={() => void copy()}
          className='inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
          aria-label={copied ? 'Copied' : 'Copy code'}
        >
          {copied ? <Check className='size-3.5' /> : <Copy className='size-3.5' />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className='overflow-x-auto p-3 font-mono text-[13px] leading-relaxed'>
        {html ? (
          <code className='hljs' dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <code>{code}</code>
        )}
      </pre>
    </div>
  )
}

const CALLOUTS = {
  note: { icon: Info, label: 'Note', className: 'border-info/40 bg-info/10', iconClass: 'text-info-strong' },
  tip: { icon: Lightbulb, label: 'Tip', className: 'border-success/40 bg-success/10', iconClass: 'text-success-strong' },
  warning: { icon: TriangleAlert, label: 'Warning', className: 'border-warning/40 bg-warning/10', iconClass: 'text-warning-strong' },
} as const

type CalloutKind = keyof typeof CALLOUTS

/** `> **Note:** …` → "note"; any other blockquote stays a quote. */
export function calloutKind(text: string): CalloutKind | null {
  const match = /^\s*(note|tip|warning)\s*:/i.exec(text)
  return match ? (match[1].toLowerCase() as CalloutKind) : null
}

function Heading({ level, children }: { level: 2 | 3; children: ReactNode }) {
  const id = slugify(textOf(children))
  const Tag = level === 2 ? 'h2' : 'h3'
  return (
    <Tag
      id={id}
      className={cn(
        'group/heading scroll-mt-20 font-semibold text-foreground',
        level === 2 ? 'mt-10 mb-3 border-b border-surface-border pb-2 text-lg' : 'mt-7 mb-2 text-base',
      )}
    >
      {children}
      <a
        href={`#${id}`}
        aria-label='Link to this section'
        className='ms-2 text-muted-foreground opacity-0 transition-opacity group-hover/heading:opacity-100 focus-visible:opacity-100'
      >
        #
      </a>
    </Tag>
  )
}

const components: Components = {
  h1: ({ children }) => <Heading level={2}>{children}</Heading>,
  h2: ({ children }) => <Heading level={2}>{children}</Heading>,
  h3: ({ children }) => <Heading level={3}>{children}</Heading>,
  h4: ({ children }) => <h4 className='mt-5 mb-2 font-semibold'>{children}</h4>,
  p: ({ children }) => <p className='my-3 leading-7'>{children}</p>,
  ul: ({ children }) => <ul className='my-3 list-disc space-y-1.5 ps-6 leading-7'>{children}</ul>,
  ol: ({ children }) => <ol className='my-3 list-decimal space-y-1.5 ps-6 leading-7'>{children}</ol>,
  li: ({ children }) => <li className='ps-1'>{children}</li>,
  hr: () => <hr className='my-8 border-surface-border' />,
  strong: ({ children }) => <strong className='font-semibold text-foreground'>{children}</strong>,
  img: ({ src, alt }) => (
    <img src={typeof src === 'string' ? src : undefined} alt={alt ?? ''} className='my-4 rounded-lg border border-surface-border' />
  ),
  a: ({ href = '', children }) => {
    const className = 'font-medium text-info-strong underline underline-offset-4 hover:no-underline'
    const internal = parseDocsHref(href)
    if (internal) {
      return internal.section && internal.slug ? (
        <Link
          {...docsLink({ section: internal.section, slug: internal.slug })}
          hash={internal.hash}
          className={className}
        >
          {children}
        </Link>
      ) : (
        <Link to='/docs' className={className}>
          {children}
        </Link>
      )
    }
    if (href.startsWith('/examples/docs/')) {
      return <a href={href} download className={className}>{children}</a>
    }
    if (href.startsWith('#')) {
      return <a href={href} className={className}>{children}</a>
    }
    return (
      <a href={href} target='_blank' rel='noreferrer noopener' className={className}>
        {children}
      </a>
    )
  },
  code: ({ className, children }) => {
    const match = /language-([\w-]+)/.exec(className ?? '')
    const text = String(children).replace(/\n$/, '')
    if (match || text.includes('\n')) {
      return <CodeBlock code={text} language={match?.[1] ?? ''} />
    }
    return (
      <code className='rounded-md border border-surface-border bg-surface-1 px-1.5 py-0.5 font-mono text-[0.85em]'>
        {children}
      </code>
    )
  },
  pre: ({ children }) => <>{children}</>,
  blockquote: ({ children }) => {
    const kind = calloutKind(textOf(children))
    if (!kind) {
      return (
        <blockquote className='my-4 border-s-2 border-surface-border ps-4 text-muted-foreground'>
          {children}
        </blockquote>
      )
    }
    const { icon: Icon, label, className, iconClass } = CALLOUTS[kind]
    return (
      <aside
        aria-label={label}
        className={cn('my-5 flex gap-3 rounded-lg border px-4 py-1', className)}
      >
        <Icon className={cn('mt-4 size-4 shrink-0', iconClass)} aria-hidden />
        <div className='min-w-0 flex-1 [&>p]:my-3'>{children}</div>
      </aside>
    )
  },
  table: ({ children }) => (
    <div className='my-5 overflow-x-auto rounded-lg border border-surface-border'>
      <table className='w-full border-collapse text-sm'>{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className='bg-surface-1'>{children}</thead>,
  th: ({ children }) => (
    <th scope='col' className='border-b border-surface-border px-3 py-2 text-start align-bottom font-medium'>
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className='border-b border-surface-border px-3 py-2 align-top leading-6 [tr:last-child_&]:border-b-0'>
      {children}
    </td>
  ),
}

export const DocsMarkdown = memo(function DocsMarkdown({ children }: { children: string }) {
  return (
    <div className='docs-markdown text-[15px] text-foreground break-words'>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  )
})
