import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { api, ApiError } from '@/lib/api-client'
import { safeBiTarget } from './bi-target'

interface BiSession {
  path: string
  superset_role: string
}

function leaveForSuperset(url: string) {
  window.location.replace(url)
}

/**
 * Opens a BI session for the signed-in Nova user, then hands the browser to
 * Superset. The session lives in an HttpOnly cookie scoped to `/bi`, so the
 * access token never reaches Superset.
 */
export function BiLaunch({
  next,
  navigate = leaveForSuperset,
}: {
  next?: string
  navigate?: (url: string) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    api
      .post<BiSession>('/superset/session', undefined, controller.signal)
      .then(() => navigate(safeBiTarget(next)))
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setError(cause instanceof ApiError ? cause.message : 'BI could not be opened')
      })
    return () => controller.abort()
  }, [next, navigate, attempt])

  return (
    <div className='flex h-svh w-full flex-col items-center justify-center gap-3 p-4 text-center'>
      {error ? (
        <>
          <h1 className='text-lg font-semibold'>BI could not be opened</h1>
          <p className='max-w-md text-sm text-muted-foreground'>{error}</p>
          <Button
            onClick={() => {
              setError(null)
              setAttempt((value) => value + 1)
            }}
          >
            Try again
          </Button>
        </>
      ) : (
        <p className='text-sm text-muted-foreground' role='status'>
          Opening BI…
        </p>
      )}
    </div>
  )
}
