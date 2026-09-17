import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/**
 * Indeterminate progress for a refresh that runs over data already on screen.
 * It floats so it never reflows the content underneath.
 */
function RefreshBanner({
  label,
  className,
  ...props
}: React.ComponentProps<'div'> & { label: string }) {
  return (
    <div
      data-slot='refresh-banner'
      role='status'
      aria-live='polite'
      className={cn(
        'pointer-events-none absolute inset-x-4 top-4 z-10 flex justify-center',
        className
      )}
      {...props}
    >
      <div className='w-full max-w-xs overflow-hidden rounded-full border bg-surface-2 shadow-sm'>
        <div className='h-1.5 w-full overflow-hidden bg-muted'>
          <div className='h-full w-1/3 animate-pulse rounded-full bg-primary motion-reduce:animate-none' />
        </div>
        <div className='px-3 py-2 text-center text-xs font-medium text-foreground'>
          {label}
        </div>
      </div>
    </div>
  )
}

/**
 * First paint of a view. Lines stand in for the rows that are coming, so the
 * layout does not jump when they arrive.
 */
function LoadingLines({
  rows = 5,
  className,
  ...props
}: React.ComponentProps<'div'> & { rows?: number }) {
  return (
    <div
      data-slot='loading-lines'
      role='status'
      aria-live='polite'
      className={cn('space-y-3 py-2', className)}
      {...props}
    >
      <span className='sr-only'>Loading</span>
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className='h-11 rounded-md' />
      ))}
    </div>
  )
}

/**
 * Blocking first load for a panel or dialog. Centres in its nearest positioned
 * ancestor and keeps `Skeleton` as the single loading primitive.
 */
function LoadingOverlay({
  label = 'Loading',
  className,
  ...props
}: React.ComponentProps<'div'> & { label?: string }) {
  return (
    <div
      data-slot='loading-overlay'
      role='status'
      aria-live='polite'
      className={cn(
        'flex min-h-[160px] flex-col items-center justify-center gap-3',
        className
      )}
      {...props}
    >
      <div className='w-full max-w-xs space-y-2.5'>
        <Skeleton className='h-4 w-3/4 rounded-md' />
        <Skeleton className='h-4 w-full rounded-md' />
        <Skeleton className='h-4 w-1/2 rounded-md' />
      </div>
      <span className='text-xs text-muted-foreground'>{label}</span>
    </div>
  )
}

export { LoadingOverlay, LoadingLines, RefreshBanner }
