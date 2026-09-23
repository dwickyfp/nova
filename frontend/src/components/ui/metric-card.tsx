import type { LucideIcon } from 'lucide-react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const metricCardVariants = cva('flex flex-col gap-1.5', {
  variants: {
    /**
     * Hierarchy is deliberate: a metric that drives a decision reads larger
     * than a supporting count. Do not uniform every card to `standard`.
     */
    weight: {
      primary: 'min-h-[128px] justify-center',
      standard: 'min-h-[108px] justify-center',
      compact: 'min-h-[84px] justify-center',
    },
  },
  defaultVariants: {
    weight: 'standard',
  },
})

const metricValueVariants = cva('font-normal tabular-nums', {
  variants: {
    weight: {
      primary: 'text-3xl sm:text-4xl',
      standard: 'text-3xl',
      compact: 'text-2xl',
    },
  },
  defaultVariants: {
    weight: 'standard',
  },
})

type MetricCardProps = React.ComponentProps<'div'> &
  VariantProps<typeof metricCardVariants> & {
    label: string
    value: React.ReactNode
    icon?: LucideIcon
    /** Tone colours the icon, never the number: the figure stays neutral. */
    tone?: 'neutral' | 'success' | 'warning' | 'info' | 'danger' | 'primary'
    /** A comparison the value is measured against, e.g. "vs previous 24h". */
    hint?: string
  }

const toneIcon = {
  neutral: 'bg-muted text-muted-foreground',
  success: 'bg-success/10 text-success-strong',
  warning: 'bg-warning/10 text-warning-strong',
  info: 'bg-info/10 text-info-strong',
  danger: 'bg-destructive/10 text-destructive',
  primary: 'bg-primary/10 text-primary',
} as const

function MetricCard({
  label,
  value,
  icon: Icon,
  tone = 'neutral',
  hint,
  weight,
  className,
  ...props
}: MetricCardProps) {
  return (
    <div
      data-slot='metric-card'
      data-weight={weight ?? 'standard'}
      className={cn(
        'rounded-xl border bg-surface-2 p-5 text-card-foreground',
        metricCardVariants({ weight }),
        className
      )}
      {...props}
    >
      <div className='flex items-center gap-3'>
        {Icon ? (
          <span
            aria-hidden='true'
            className={cn(
              'flex size-9 shrink-0 items-center justify-center rounded-lg',
              toneIcon[tone]
            )}
          >
            <Icon className='size-4' />
          </span>
        ) : null}
        <p className='text-sm font-medium text-muted-foreground'>{label}</p>
      </div>
      <p className={cn('text-foreground', metricValueVariants({ weight }))}>
        {value}
      </p>
      {hint ? <p className='text-xs text-muted-foreground'>{hint}</p> : null}
    </div>
  )
}

export { MetricCard }
