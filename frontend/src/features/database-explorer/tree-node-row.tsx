import {
  Box,
  ChevronRight,
} from 'lucide-react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import { SidebarMenuItem, SidebarMenuSub } from '@/components/ui/sidebar'
import { cn } from '@/lib/utils'
import { getNodeIcon } from './helpers'
import type { ExplorerNode } from './types'

export type TreeNodeRowProps = {
  node: ExplorerNode
  expandedIds: Set<string>
  selectedId: string
  onToggle: (id: string) => void
  onSelect: (id: string) => void
  onCreateStage?: (database: string) => void
}

export function TreeNodeRow({
  node,
  expandedIds,
  selectedId,
  onToggle,
  onSelect,
  onCreateStage,
}: TreeNodeRowProps) {
  const hasChildren = Boolean(node.children?.length)
  const isExpanded = expandedIds.has(node.id)
  const isSelected = selectedId === node.id
  const Icon = getNodeIcon(node.type)

  // Empty placeholder node (e.g. "No Objects Found")
  if (node.label === 'No Objects Found') {
    return (
      <SidebarMenuItem key={node.id}>
        <div className='flex items-center gap-2 px-2 py-1.5 text-sm italic text-muted-foreground/60'>
          <span className='ml-4'>{node.label}</span>
        </div>
      </SidebarMenuItem>
    )
  }

  if (hasChildren) {
    const isStagesGroup = node.label === 'Stages' && node.type === 'group'

    const collapsibleContent = (
      <Collapsible
        key={node.id}
        open={isExpanded}
        onOpenChange={() => {
          onToggle(node.id)
          onSelect(node.id)
        }}
      >
        <SidebarMenuItem className='min-w-0'>
          <CollapsibleTrigger asChild>
            <button
              type='button'
              className={cn(
                'flex min-w-0 w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted',
                isSelected && 'bg-primary/10 font-medium text-primary dark:text-foreground',
              )}
            >
              <ChevronRight
                className={cn(
                  'size-4 shrink-0 transition-transform duration-200',
                  isExpanded && 'rotate-90',
                )}
              />
              <Icon
                className={cn(
                  'size-4 shrink-0',
                  isSelected ? 'text-primary dark:text-foreground' : 'text-muted-foreground',
                )}
              />
              <span className='flex-1 min-w-0 truncate text-left'>{node.label}</span>
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <SidebarMenuSub>
              {node.children?.map((child) => (
                <TreeNodeRow
                  key={child.id}
                  node={child}
                  expandedIds={expandedIds}
                  selectedId={selectedId}
                  onToggle={onToggle}
                  onSelect={onSelect}
                  onCreateStage={onCreateStage}
                />
              ))}
            </SidebarMenuSub>
          </CollapsibleContent>
        </SidebarMenuItem>
      </Collapsible>
    )

    if (isStagesGroup && node.database && onCreateStage) {
      return (
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div>{collapsibleContent}</div>
          </ContextMenuTrigger>
          <ContextMenuContent>
            <ContextMenuItem onClick={() => onCreateStage(node.database!)}>
              <Box className='mr-2 h-4 w-4' />
              Create Stage
            </ContextMenuItem>
          </ContextMenuContent>
        </ContextMenu>
      )
    }

    return collapsibleContent
  }

  return (
    <SidebarMenuItem key={node.id} className='min-w-0'>
      <button
        type='button'
        className={cn(
          'flex min-w-0 w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-muted',
          isSelected && 'bg-primary/10 font-medium text-primary dark:text-foreground',
        )}
        onClick={() => onSelect(node.id)}
      >
        <span className='flex h-4 w-4 shrink-0 items-center justify-center'>
          <span className='h-1.5 w-1.5 rounded-full bg-border' />
        </span>
        <Icon
          className={cn(
            'size-4 shrink-0',
            isSelected ? 'text-primary dark:text-foreground' : 'text-muted-foreground',
          )}
        />
        <span className='flex-1 min-w-0 truncate text-left'>{node.label}</span>
      </button>
    </SidebarMenuItem>
  )
}
