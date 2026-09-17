import { cn } from '@/lib/utils'

type PageHeaderProps = React.ComponentProps<'div'> & {
  title: string
  description?: string
  /** Page-level actions, aligned to the trailing edge. */
  actions?: React.ReactNode
}

function PageHeader({
  title,
  description,
  actions,
  className,
  children,
  ...props
}: PageHeaderProps) {
  return (
    <div
      data-slot='page-header'
      className={cn(
        'flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between',
        className
      )}
      {...props}
    >
      <div className='min-w-0'>
        <h1 className='text-lg font-medium'>{title}</h1>
        {description ? (
          <p className='mt-1 text-sm text-muted-foreground'>{description}</p>
        ) : null}
        {children}
      </div>
      {actions ? (
        <div className='flex shrink-0 items-center gap-2'>{actions}</div>
      ) : null}
    </div>
  )
}

export { PageHeader }
