import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

export type StatusTone =
  | 'success'
  | 'warning'
  | 'info'
  | 'danger'
  | 'neutral'
  | 'primary'

const statusBadgeVariants = cva(
  'inline-flex w-fit shrink-0 items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap [&>svg]:size-3 [&>svg]:pointer-events-none',
  {
    variants: {
      tone: {
        success:
          'border-success/25 bg-success/10 text-success-strong dark:border-success/30 dark:bg-success/15 dark:text-success-strong',
        warning:
          'border-warning/30 bg-warning/10 text-warning-strong dark:border-warning/35 dark:bg-warning/15 dark:text-warning-strong',
        info: 'border-info/25 bg-info/10 text-info-strong dark:border-info/30 dark:bg-info/15 dark:text-info-strong',
        danger:
          'border-destructive/25 bg-destructive/10 text-destructive dark:border-destructive/35 dark:bg-destructive/15',
        neutral: 'border-border bg-muted text-muted-foreground',
        primary:
          'border-primary/25 bg-primary/10 text-primary dark:border-primary/35 dark:bg-primary/15',
      },
    },
    defaultVariants: {
      tone: 'neutral',
    },
  }
)

type StatusBadgeProps = React.ComponentProps<'span'> &
  VariantProps<typeof statusBadgeVariants> & {
    /** Dot marks a live state. Omit it for a static classification. */
    dot?: boolean
  }

function StatusBadge({
  className,
  tone,
  dot = false,
  children,
  ...props
}: StatusBadgeProps) {
  return (
    <span
      data-slot='status-badge'
      className={cn(statusBadgeVariants({ tone }), className)}
      {...props}
    >
      {dot ? (
        <span
          aria-hidden='true'
          data-slot='status-badge-dot'
          className='size-1.5 shrink-0 rounded-full bg-current'
        />
      ) : null}
      {children}
    </span>
  )
}

export { StatusBadge }
