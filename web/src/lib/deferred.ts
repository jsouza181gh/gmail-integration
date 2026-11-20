import { useCallback, useEffect, useRef, useState } from 'react'

interface Scheduled {
  label: string
  run: () => Promise<void>
  timer: ReturnType<typeof setTimeout>
}

export interface Deferred {
  schedule: (label: string, run: () => Promise<void>) => void
  cancel: () => void
  pending: { label: string } | null
}

/**
 * Runs `run` after `delayMs` unless cancelled. Leaving the screen (unmount) or
 * scheduling another action sends the pending one at once: a decision is never
 * silently dropped, only delayed.
 */
export function useDeferred(delayMs: number): Deferred {
  const current = useRef<Scheduled | null>(null)
  const [pending, setPending] = useState<{ label: string } | null>(null)

  const flush = useCallback(() => {
    const s = current.current
    if (!s) return
    clearTimeout(s.timer)
    current.current = null
    setPending(null)
    void s.run()
  }, [])

  const schedule = useCallback(
    (label: string, run: () => Promise<void>) => {
      flush()
      const timer = setTimeout(flush, delayMs)
      current.current = { label, run, timer }
      setPending({ label })
    },
    [delayMs, flush],
  )

  const cancel = useCallback(() => {
    const s = current.current
    if (!s) return
    clearTimeout(s.timer)
    current.current = null
    setPending(null)
  }, [])

  useEffect(() => flush, [flush])

  return { schedule, cancel, pending }
}
