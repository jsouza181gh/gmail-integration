import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useHotkey } from '../lib/hotkeys'
import { Icon } from './Icon'

interface ShowOptions {
  undo?: (() => void) | undefined
  ms?: number | undefined
}

interface ToastApi {
  show: (text: string, opts?: ShowOptions) => void
}

interface Current {
  id: number
  text: string
  undo?: (() => void) | undefined
  ms: number
}

const ToastContext = createContext<ToastApi | null>(null)

export function useToast(): ToastApi {
  const api = useContext(ToastContext)
  if (!api) throw new Error('useToast fora de <ToastProvider>')
  return api
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<Current | null>(null)
  const seq = useRef(0)

  const show = useCallback((text: string, opts: ShowOptions = {}) => {
    seq.current += 1
    setCurrent({ id: seq.current, text, undo: opts.undo, ms: opts.ms ?? (opts.undo ? 5000 : 4000) })
  }, [])

  useEffect(() => {
    if (!current) return
    // Hide only this toast: a newer one may already have replaced it.
    const timer = setTimeout(() => setCurrent((c) => (c?.id === current.id ? null : c)), current.ms)
    return () => clearTimeout(timer)
  }, [current])

  const undo = useCallback(() => {
    current?.undo?.()
    setCurrent(null)
  }, [current])

  useHotkey('z', undo, Boolean(current?.undo))

  const api = useMemo<ToastApi>(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-host" role="status" aria-live="polite">
        {current ? (
          <div className="ia-toast">
            <span className="ia-ico-accent">
              <Icon name="check" />
            </span>
            <span>{current.text}</span>
            {current.undo ? (
              <button type="button" className="ia-btn ia-btn--ghost ia-btn--sm" onClick={undo}>
                Desfazer<span className="ia-kbd">Z</span>
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </ToastContext.Provider>
  )
}
