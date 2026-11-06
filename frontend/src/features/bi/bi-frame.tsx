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
 * A new path or `version` swaps views without an empty frame between them.
 *
 * Superset reads its theme only when a page loads. A view reloads to follow a
 * theme change; an editor (`holdsEdits`) never does, since that would discard
 * unsaved Superset changes, and takes the new theme on its next load.
 */
export const BiFrame = forwardRef<
  HTMLIFrameElement,
  {
    path: string
    title: string
    onLoad?: () => void
    holdsEdits?: boolean
    /** A change reloads the view even when its path stays the same. */
    version?: string
  }
>(function BiFrame({ path, title, onLoad, holdsEdits = false, version }, ref) {
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
    <LayeredFrame
      key={themeKey}
      ref={ref}
      src={safeBiTarget(path)}
      version={version}
      title={title}
      onLoad={onLoad}
    />
  )
})

type Layer = { id: number; src: string; shown: boolean }

/** How long a new view fades in over the one it replaces. */
const SWAP_FADE_MS = 180

/**
 * One or two stacked frames. A new `src` or `version` loads under the view
 * already shown, which covers it until the new one has drawn, then fades out:
 * a change never passes through an empty frame. The newest frame stays in the
 * layout (it sizes the view); the one it replaces lies over it. Nova's loading
 * state is shown only before the first view has drawn.
 */
const LayeredFrame = forwardRef<
  HTMLIFrameElement,
  { src: string; version?: string; title: string; onLoad?: () => void }
>(function LayeredFrame({ src, version = '', title, onLoad }, ref) {
  const [layers, setLayers] = useState<Layer[]>([{ id: 0, src, shown: false }])
  const nextId = useRef(1)
  const target = `${src}\u0000${version}`
  const current = useRef(target)

  useEffect(() => {
    if (target === current.current) return
    current.current = target
    const id = nextId.current++
    // Keep the view on screen (the newest shown) and drop any older swap.
    setLayers((all) => [...all.filter((layer) => layer.shown).slice(-1), { id, src, shown: false }])
  }, [target, src])

  const reveal = (id: number) => {
    setLayers((all) => all.map((layer) => (layer.id === id ? { ...layer, shown: true } : layer)))
    window.setTimeout(
      () => setLayers((all) => all.filter((layer) => layer.id >= id)),
      SWAP_FADE_MS,
    )
  }

  const newest = layers[layers.length - 1]
  // A replacement is loading under a view already on screen: that view dims
  // and says so, rather than standing in for the change.
  const updating = !newest.shown && layers.some((layer) => layer.shown)
  return (
    <div className='relative flex min-h-0 flex-1 flex-col'>
      {layers.map((layer) => (
        <FrameLayer
          key={layer.id}
          ref={layer.id === newest.id ? ref : undefined}
          src={layer.src}
          title={title}
          current={layer.id === newest.id}
          shown={layer.shown}
          replaced={layer.id !== newest.id && newest.shown}
          dimmed={layer.id !== newest.id && updating}
          onLoad={layer.id === newest.id ? onLoad : undefined}
          onDrawn={() => reveal(layer.id)}
        />
      ))}
      {updating ? (
        <p
          role='status'
          className='absolute right-3 top-3 z-20 flex items-center gap-1.5 rounded-md border bg-background/95 px-2 py-1 text-xs text-muted-foreground shadow-sm'
        >
          <Loader2 aria-hidden='true' className='size-3.5 animate-spin' />
          Updating
        </p>
      ) : null}
      {layers.some((layer) => layer.shown) ? null : <FrameLoading overlay />}
    </div>
  )
})

const FrameLayer = forwardRef<
  HTMLIFrameElement,
  {
    src: string
    title: string
    current: boolean
    shown: boolean
    /** A view whose replacement has drawn: it fades out, then is removed. */
    replaced: boolean
    /** A view whose replacement is still loading under it. */
    dimmed: boolean
    onLoad?: () => void
    onDrawn: () => void
  }
>(function FrameLayer({ src, title, current, shown, replaced, dimmed, onLoad, onDrawn }, ref) {
  const [loaded, setLoaded] = useState(false)
  const frame = useRef<HTMLIFrameElement | null>(null)
  const drawn = useRef(onDrawn)
  drawn.current = onDrawn

  // The page loads before its charts finish querying. Wait for Superset's
  // spinner to go, so its loading state never shows after Nova's.
  useEffect(() => {
    if (!loaded || shown) return
    const started = Date.now()
    const timer = window.setInterval(() => {
      if (!stillDrawing(frame.current) || Date.now() - started >= FIRST_DRAW_LIMIT_MS) {
        window.clearInterval(timer)
        drawn.current()
      }
    }, FIRST_DRAW_POLL_MS)
    return () => window.clearInterval(timer)
  }, [loaded, shown])

  return (
    <iframe
      ref={(node) => {
        frame.current = node
        if (typeof ref === 'function') ref(node)
        else if (ref) ref.current = node
      }}
      src={src}
      // The view being replaced leaves the accessibility tree at once.
      title={current ? title : undefined}
      aria-hidden={current ? undefined : true}
      tabIndex={current ? undefined : -1}
      className={cn(
        'border-0 bg-background transition-opacity ease-out',
        current ? 'min-h-0 w-full flex-1' : 'absolute inset-0 z-10 h-full w-full',
        !shown || replaced ? 'opacity-0' : dimmed ? 'opacity-50' : 'opacity-100',
      )}
      style={{ transitionDuration: `${SWAP_FADE_MS}ms` }}
      onLoad={() => {
        setLoaded(true)
        onLoad?.()
      }}
    />
  )
})

export function FrameLoading({ overlay = false }: { overlay?: boolean }) {
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
