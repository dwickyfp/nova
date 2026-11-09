import type { ReactNode } from 'react'
import { PageHeader } from '@/components/ui/page-header'

/**
 * A monitoring view's header. On its own page it is the page title; inside
 * the Queries tabs the tab already names the view, so only the one-line
 * description and the actions remain.
 */
export function MonitoringHeader({
  embedded = false,
  title,
  description,
  actions,
}: {
  embedded?: boolean
  title: string
  description: string
  actions?: ReactNode
}) {
  if (!embedded) {
    return <PageHeader title={title} description={description} actions={actions} />
  }
  return (
    <div className='flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between'>
      <p className='text-sm text-muted-foreground'>{description}</p>
      {actions ? <div className='flex shrink-0 items-center gap-2'>{actions}</div> : null}
    </div>
  )
}
