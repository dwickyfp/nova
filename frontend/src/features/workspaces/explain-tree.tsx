import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Operator colours group by role, not by literal colour: scan and exchange
 * operators read as informational, joins as a warning-level cost, and sinks as
 * neutral. Assigning per-operator shades would add variety without meaning.
 */
const OPERATOR_COLORS: Record<string, string> = {
  OlapScanNode: 'text-info-strong',
  OlapScan: 'text-info-strong',
  EXCHANGE: 'text-success-strong',
  'HASH JOIN': 'text-warning-strong',
  'NESTLOOP JOIN': 'text-warning-strong',
  'MERGE JOIN': 'text-warning-strong',
  JOIN: 'text-warning-strong',
  AGGREGATE: 'text-primary',
  AGGREGATE_NODE: 'text-primary',
  SORT: 'text-warning-strong',
  'TOP-N': 'text-warning-strong',
  ANALYTIC: 'text-primary',
  'RESULT SINK': 'text-muted-foreground',
  'STREAM DATA SINK': 'text-muted-foreground',
  UNION: 'text-info-strong',
  INTERSECT: 'text-info-strong',
  EXCEPT: 'text-info-strong',
  FILTER: 'text-warning-strong',
  PROJECT: 'text-warning-strong',
}

function getOperatorColor(line: string): string {
  // Match operator patterns like "0:OlapScanNode", "1:EXCHANGE", "HASH JOIN", etc.
  const opMatch = line.match(/^\d+:(\w+)/)
  if (opMatch && OPERATOR_COLORS[opMatch[1]]) return OPERATOR_COLORS[opMatch[1]]
  for (const [op, color] of Object.entries(OPERATOR_COLORS)) {
    if (line.trim().startsWith(op)) return color
  }
  return 'text-foreground'
}

interface PlanNode {
  line: string
  indent: number
  children: PlanNode[]
}

function parseExplainPlan(text: string): PlanNode[] {
  const lines = text.split('\n')
  const root: PlanNode = { line: '', indent: -1, children: [] }
  const stack: PlanNode[] = [root]

  for (const rawLine of lines) {
    if (rawLine.trim() === '') continue
    const indent = rawLine.search(/\S/)
    const line = rawLine.trim()
    const node: PlanNode = { line, indent, children: [] }

    while (stack.length > 1 && stack[stack.length - 1].indent >= indent) {
      stack.pop()
    }
    stack[stack.length - 1].children.push(node)
    stack.push(node)
  }

  return root.children
}

export function ExplainTreeView({ planText }: { planText: string }) {
  const tree = useMemo(() => parseExplainPlan(planText), [planText])
  const [collapsedFragments, setCollapsedFragments] = useState<Set<string>>(
    new Set()
  )

  function toggleFragment(label: string) {
    setCollapsedFragments((prev) => {
      const next = new Set(prev)
      if (next.has(label)) {
        next.delete(label)
      } else {
        next.add(label)
      }
      return next
    })
  }

  // Group top-level nodes into PLAN FRAGMENTs
  const fragments: { label: string; children: PlanNode[] }[] = []
  let currentFragment: { label: string; children: PlanNode[] } | null = null

  for (const node of tree) {
    if (node.line.startsWith('PLAN FRAGMENT')) {
      currentFragment = { label: node.line, children: node.children }
      fragments.push(currentFragment)
    } else if (currentFragment) {
      currentFragment.children.push(node)
    } else {
      currentFragment = { label: node.line, children: [] }
      fragments.push(currentFragment)
      currentFragment.children = node.children
    }
  }

  if (!fragments.length) {
    return (
      <div className='p-3 text-xs text-muted-foreground'>
        No plan output available.
      </div>
    )
  }

  return (
    <div className='space-y-1 font-mono text-xs'>
      {fragments.map((fragment) => {
        const collapsed = collapsedFragments.has(fragment.label)
        return (
          <div key={fragment.label} className='rounded border border-border'>
            <button
              type='button'
              className='flex w-full items-center gap-1.5 bg-muted/40 px-2 py-1.5 text-left font-medium'
              onClick={() => toggleFragment(fragment.label)}
              aria-expanded={!collapsed}
            >
              {collapsed ? (
                <ChevronRight className='size-3.5 shrink-0' />
              ) : (
                <ChevronDown className='size-3.5 shrink-0' />
              )}
              <span className='truncate'>{fragment.label}</span>
            </button>
            {!collapsed && (
              <div className='p-2'>
                {fragment.children.map((node, index) => (
                  <ExplainNode key={`${node.line}-${index}`} node={node} depth={0} />
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function ExplainNode({ node, depth }: { node: PlanNode; depth: number }) {
  const [collapsed, setCollapsed] = useState(false)
  const hasChildren = node.children.length > 0

  return (
    <div style={{ paddingLeft: depth * 16 }}>
      <div className='flex items-start gap-1 py-0.5'>
        {hasChildren ? (
          <button
            type='button'
            onClick={() => setCollapsed((value) => !value)}
            className='mt-0.5 shrink-0 text-muted-foreground hover:text-foreground'
            aria-label={collapsed ? 'Expand operator' : 'Collapse operator'}
            aria-expanded={!collapsed}
          >
            {collapsed ? (
              <ChevronRight className='size-3' />
            ) : (
              <ChevronDown className='size-3' />
            )}
          </button>
        ) : (
          <span className='mt-0.5 size-3 shrink-0' aria-hidden='true' />
        )}
        <span className={cn('whitespace-pre-wrap', getOperatorColor(node.line))}>
          {node.line}
        </span>
      </div>
      {!collapsed &&
        node.children.map((child, index) => (
          <ExplainNode
            key={`${child.line}-${index}`}
            node={child}
            depth={depth + 1}
          />
        ))}
    </div>
  )
}
