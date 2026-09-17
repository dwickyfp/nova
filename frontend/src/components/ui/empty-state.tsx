import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

type EmptyStateProps = React.ComponentProps<'div'> & {
  icon?: LucideIcon
  title: string
  /** Why the view is empty. A cause, not a restatement of "no data". */
  description?: string
  /** The next step. Omit only when no action genuinely exists. */
  action?: React.ReactNode
  /** `error` keeps the same structure but signals a failed load. */
  variant?: 'default' | 'error'
}

function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  variant = 'default',
  className,
  ...props
}: EmptyStateProps) {
  return (
    <div
      data-slot='empty-state'
      data-variant={variant}
      role={variant === 'error' ? 'alert' : undefined}
      className={cn(
        'flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-10 text-center',
        variant === 'error'
          ? 'border-destructive/35 bg-destructive/5'
          : 'border-border',
        className
      )}
      {...props}
    >
      {Icon ? (
        <Icon
          aria-hidden='true'
          className={cn(
            'mb-3 size-5',
            variant === 'error' ? 'text-destructive' : 'text-muted-foreground'
          )}
        />
      ) : null}
      <p
        className={cn(
          'text-sm font-medium',
          variant === 'error' ? 'text-destructive' : 'text-foreground'
        )}
      >
        {title}
      </p>
      {description ? (
        <p className='mt-1 max-w-sm text-xs text-muted-foreground'>
          {description}
        </p>
      ) : null}
      {action ? <div className='mt-4'>{action}</div> : null}
    </div>
  )
}

export { EmptyState }
