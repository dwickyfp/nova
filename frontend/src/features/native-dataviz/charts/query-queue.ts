/**
 * A per-page bound on concurrent dataset queries. The server admits a few
 * queries per person at a time; a dashboard with many widgets queues here
 * instead of being refused. A caller that is aborted while waiting never sends.
 */
export const MAX_CONCURRENT_QUERIES = 3

type Waiter = { start: () => void; signal?: AbortSignal; cancel: () => void }

let running = 0
const waiting: Waiter[] = []

function next() {
  while (running < MAX_CONCURRENT_QUERIES && waiting.length) {
    const waiter = waiting.shift()!
    if (waiter.signal?.aborted) continue
    waiter.start()
  }
}

export function queuedQuery<T>(run: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const abort = () => {
      const index = waiting.indexOf(waiter)
      if (index >= 0) waiting.splice(index, 1)
      reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'))
    }
    const waiter: Waiter = {
      signal,
      cancel: abort,
      start: () => {
        signal?.removeEventListener('abort', abort)
        running += 1
        run().then(resolve, reject).finally(() => {
          running -= 1
          next()
        })
      },
    }
    if (signal?.aborted) return abort()
    signal?.addEventListener('abort', abort, { once: true })
    waiting.push(waiter)
    next()
  })
}

/** Capacity refusals are transient: retry with jittered exponential backoff. */
export function capacityRetry(failureCount: number, error: unknown): boolean {
  return failureCount < 5 && typeof error === 'object' && error !== null
    && 'status' in error && (error as { status: number }).status === 429
}

export function capacityDelay(attempt: number): number {
  return Math.min(4000, 300 * 2 ** attempt) + Math.floor(Math.random() * 200)
}
