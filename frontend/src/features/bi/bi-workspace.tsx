import { useCallback, useEffect, useRef, useState } from 'react'
import { ExternalLink, RotateCw } from 'lucide-react'
import { Logo } from '@/assets/logo'
import { Header } from '@/components/layout/header'
import { Button } from '@/components/ui/button'
import { api, ApiError } from '@/lib/api-client'
import { safeBiTarget } from './bi-target'

type Status = 'opening' | 'ready' | 'error'

/** The Superset path a framed page shows, or null when it left Superset. */
function framedPath(frame: HTMLIFrameElement | null): string | null {
  try {
    const location = frame?.contentWindow?.location
    if (!location) return null
    const path = `${location.pathname}${location.search}`
    return path.startsWith('/bi/') ? path : null
  } catch {
    return null
  }
}

/**
 * Superset inside Nova's own frame. A BI session is opened first, so the
 * frame is signed in by its HttpOnly `/bi` cookie; the page then follows the
 * frame's navigation in `path` so a reload or shared link returns to the same
 * Superset view.
 */
export function BiWorkspace({
  path,
  onPathChange,
}: {
  path?: string
  onPathChange: (path: string) => void
}) {
  const [source] = useState(() => safeBiTarget(path))
  const [status, setStatus] = useState<Status>('opening')
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [current, setCurrent] = useState(source)
  const frame = useRef<HTMLIFrameElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    setStatus('opening')
    api
      .post('/superset/session', undefined, controller.signal)
      .then(() => setStatus('ready'))
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setError(cause instanceof ApiError ? cause.message : 'BI could not be opened')
        setStatus('error')
      })
    return () => controller.abort()
  }, [attempt])

  const followFrame = useCallback(() => {
    const next = framedPath(frame.current)
    if (!next) return
    setCurrent(next)
    if (next !== path) onPathChange(next)
  }, [onPathChange, path])

  // Superset also moves between views with client-side routing, which fires
  // no load event; check the framed location while the frame is open.
  useEffect(() => {
    if (status !== 'ready') return
    const timer = window.setInterval(followFrame, 1000)
    return () => window.clearInterval(timer)
  }, [status, followFrame])

  return (
    <div data-layout='fixed' className='flex h-full min-h-0 flex-col'>
      <Header fixed>
        <div className='flex min-w-0 flex-1 items-center gap-3'>
          <Logo sizes='40px' className='h-6 w-6 shrink-0' />
          <div className='min-w-0'>
            <h1 className='truncate text-lg font-semibold'>BI</h1>
            <p className='truncate text-sm text-muted-foreground'>
              Dashboards, charts and SQL on governed Nova data
            </p>
          </div>
        </div>
        <div className='ml-auto flex items-center gap-2'>
          <Button
            variant='outline'
            size='icon'
            className='h-8 w-8'
            aria-label='Reload BI'
            disabled={status !== 'ready'}
            onClick={() => frame.current?.contentWindow?.location.reload()}
          >
            <RotateCw className='h-3.5 w-3.5' />
          </Button>
          <Button variant='outline' size='sm' className='h-8' asChild>
            <a
              href={`/bi-launch?next=${encodeURIComponent(current)}`}
              target='_blank'
              rel='noopener'
            >
              <ExternalLink className='mr-1.5 h-3.5 w-3.5' />
              Open in new tab
            </a>
          </Button>
        </div>
      </Header>

      <div className='flex min-h-0 flex-1 flex-col overflow-hidden border-t'>
        {status === 'ready' ? (
          <iframe
            ref={frame}
            src={source}
            title='BI workspace'
            className='min-h-0 w-full flex-1 border-0 bg-background'
            onLoad={followFrame}
          />
        ) : status === 'error' ? (
          <div className='flex flex-1 flex-col items-center justify-center gap-3 p-4 text-center'>
            <h2 className='text-base font-semibold'>BI could not be opened</h2>
            <p className='max-w-md text-sm text-muted-foreground'>{error}</p>
            <Button onClick={() => setAttempt((value) => value + 1)}>Try again</Button>
          </div>
        ) : (
          <p
            className='flex flex-1 items-center justify-center text-sm text-muted-foreground'
            role='status'
          >
            Opening BI…
          </p>
        )}
      </div>
    </div>
  )
}
