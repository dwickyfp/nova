import { forwardRef, useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/context/theme-provider'
import { cn } from '@/lib/utils'
import { safeBiTarget } from './bi-target'
import { FIRST_DRAW_LIMIT_MS, FIRST_DRAW_POLL_MS, stillDrawing } from './superset-loading'
import { syncBiTheme, useBiSession } from './use-bi-session'

/**
 * A Superset view framed inside a Nova surface. It fills its parent, which
 * owns the size; the frame follows Nova's theme and is signed in by the BI
 * session it opens first. Nova's loading state covers the frame until the
 * Superset page has loaded and drawn its first charts, so a view never shows
 * two loaders in a row.
 *
 * Superset reads its theme only when a page loads. A view reloads to follow a
 * theme change; an editor (`holdsEdits`) never does, since that would discard
 * unsaved Superset changes, and takes the new theme on its next load.
 */
export const BiFrame = forwardRef<
  HTMLIFrameElement,
  { path: string; title: string; onLoad?: () => void; holdsEdits?: boolean }
>(function BiFrame({ path, title, onLoad, holdsEdits = false }, ref) {
  const session = useBiSession()
  const { theme, resolvedTheme } = useTheme()

  if (session.status === 'error') {
    return (
      <div className='flex flex-1 flex-col items-center justify-center gap-3 p-4 text-center'>
        <h2 className='text-base font-semibold'>This view could not be opened</h2>
        <p className='max-w-md text-sm text-muted-foreground'>{session.error}</p>
        <Button onClick={session.retry}>Try again</Button>
      </div>
    )
  }
  if (session.status === 'opening') return <FrameLoading />

  syncBiTheme(theme === 'system' ? 'system' : resolvedTheme)
  // In system mode Superset follows the OS on its own, as Nova does.
  const themeKey = holdsEdits ? 'editor' : theme === 'system' ? 'system' : resolvedTheme
  return (
    <LoadingFrame
      key={themeKey}
      ref={ref}
      src={safeBiTarget(path)}
      title={title}
      onLoad={onLoad}
    />
  )
})

const LoadingFrame = forwardRef<
  HTMLIFrameElement,
  { src: string; title: string; onLoad?: () => void }
>(function LoadingFrame({ src, title, onLoad }, ref) {
  const [loaded, setLoaded] = useState(false)
  const [shown, setShown] = useState(false)
  const frame = useRef<HTMLIFrameElement | null>(null)

  // The page loads before its charts finish querying. Keep Nova's loading
  // state until Superset's spinner is gone, so the two never follow each other.
  useEffect(() => {
    if (!loaded || shown) return
    const started = Date.now()
    const timer = window.setInterval(() => {
      if (!stillDrawing(frame.current) || Date.now() - started >= FIRST_DRAW_LIMIT_MS) {
        window.clearInterval(timer)
        setShown(true)
      }
    }, FIRST_DRAW_POLL_MS)
    return () => window.clearInterval(timer)
  }, [loaded, shown])

  return (
    <div className='relative flex min-h-0 flex-1 flex-col'>
      <iframe
        ref={(node) => {
          frame.current = node
          if (typeof ref === 'function') ref(node)
          else if (ref) ref.current = node
        }}
        src={src}
        title={title}
        className={cn(
          'min-h-0 w-full flex-1 border-0 bg-background transition-opacity duration-150',
          shown ? 'opacity-100' : 'opacity-0',
        )}
        onLoad={() => {
          setLoaded(true)
          onLoad?.()
        }}
      />
      {shown ? null : <FrameLoading overlay />}
    </div>
  )
})

function FrameLoading({ overlay = false }: { overlay?: boolean }) {
  return (
    <p
      className={cn(
        'flex items-center justify-center gap-2 text-sm text-muted-foreground',
        overlay ? 'absolute inset-0 bg-background' : 'flex-1',
      )}
      role='status'
    >
      <Loader2 aria-hidden='true' className='size-4 animate-spin' />
      Loading
    </p>
  )
}
