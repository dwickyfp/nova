import { forwardRef } from 'react'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/context/theme-provider'
import { safeBiTarget } from './bi-target'
import { syncBiTheme, useBiSession } from './use-bi-session'

/**
 * A Superset view framed inside a Nova surface. It fills its parent, which
 * owns the size; the frame follows Nova's theme and is signed in by the BI
 * session it opens first.
 */
export const BiFrame = forwardRef<
  HTMLIFrameElement,
  { path: string; title: string; onLoad?: () => void }
>(function BiFrame({ path, title, onLoad }, ref) {
  const session = useBiSession()
  const { resolvedTheme } = useTheme()

  if (session.status === 'error') {
    return (
      <div className='flex flex-1 flex-col items-center justify-center gap-3 p-4 text-center'>
        <h2 className='text-base font-semibold'>BI could not be opened</h2>
        <p className='max-w-md text-sm text-muted-foreground'>{session.error}</p>
        <Button onClick={session.retry}>Try again</Button>
      </div>
    )
  }
  if (session.status === 'opening') {
    return (
      <p
        className='flex flex-1 items-center justify-center text-sm text-muted-foreground'
        role='status'
      >
        Opening BI…
      </p>
    )
  }
  syncBiTheme(resolvedTheme)
  return (
    <iframe
      ref={ref}
      // A theme change reloads the frame so Superset reads the new mode.
      key={resolvedTheme}
      src={safeBiTarget(path)}
      title={title}
      className='min-h-0 w-full flex-1 border-0 bg-background'
      onLoad={onLoad}
    />
  )
})
