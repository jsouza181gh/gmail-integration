import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { canRecord, MAX_RECORDING_MS, startRecording, type Recording } from '../../lib/recorder'
import { Icon } from '../../ui/Icon'

export const TEXT_MAX = 4000

export function duration(ms: number): string {
  const s = Math.round(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

interface Props {
  text: string
  onText: (text: string) => void
  busy: boolean
  onSend: (text: string) => Promise<boolean>
  onAudio: (wav: Blob) => Promise<boolean>
  onError: (message: string) => void
}

export function Composer({ text, onText, busy, onSend, onAudio, onError }: Props) {
  const [recording, setRecording] = useState<Recording | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [sending, setSending] = useState(false)
  const box = useRef<HTMLTextAreaElement>(null)
  const stopRef = useRef<() => void>(() => {})

  useEffect(() => () => recording?.cancel(), [recording])

  const send = async () => {
    const clean = text.trim()
    if (!clean || busy || sending) return
    setSending(true)
    if (await onSend(clean.slice(0, TEXT_MAX))) onText('')
    setSending(false)
    box.current?.focus()
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void send()
    }
  }

  const stop = async () => {
    const rec = recording
    if (!rec) return
    setRecording(null)
    const { blob, ms } = await rec.stop()
    if (ms < 500) {
      onError('Áudio curto demais.')
      return
    }
    setSending(true)
    await onAudio(blob)
    setSending(false)
  }
  useEffect(() => {
    stopRef.current = () => void stop()
  })

  const record = async () => {
    if (!canRecord()) {
      onError('Microfone indisponível neste navegador.')
      return
    }
    try {
      setElapsed(0)
      const rec = await startRecording({
        maxMs: MAX_RECORDING_MS,
        onTick: setElapsed,
        onLimit: () => stopRef.current(),
      })
      setRecording(rec)
    } catch {
      onError('Microfone indisponível neste navegador.')
    }
  }

  const discard = () => {
    recording?.cancel()
    setRecording(null)
  }

  return (
    <div className="chat-composer">
      {recording ? (
        <>
          <button type="button" className="ia-btn ia-btn--ghost ia-btn--icon chat-round" aria-label="Descartar áudio" onClick={discard}>
            <Icon name="x" />
          </button>
          <span className="chat-composer__rec" role="status">
            <span className="chat-rec-dot" aria-hidden="true" />
            {`Gravando ${duration(elapsed)} de ${duration(MAX_RECORDING_MS)}`}
          </span>
          <button
            type="button"
            className="ia-btn ia-btn--primary ia-btn--icon chat-round"
            aria-label="Parar e enviar áudio"
            onClick={() => void stop()}
          >
            <Icon name="arrowup" />
          </button>
        </>
      ) : (
        <>
          <label className="chat-composer__field">
            <textarea
              ref={box}
              rows={1}
              maxLength={TEXT_MAX}
              value={text}
              onChange={(e) => onText(e.target.value)}
              onKeyDown={onKey}
              placeholder="Pergunte ou dite uma regra…"
              aria-label="Mensagem"
              className="chat-composer__input"
            />
          </label>
          <button
            type="button"
            className="ia-btn ia-btn--ghost ia-btn--icon chat-round"
            aria-label="Gravar áudio"
            disabled={busy || sending}
            onClick={() => void record()}
          >
            <Icon name="mic" />
          </button>
          <button
            type="button"
            className="ia-btn ia-btn--primary ia-btn--icon chat-round"
            aria-label="Enviar"
            disabled={busy || sending || !text.trim()}
            onClick={() => void send()}
          >
            <Icon name="arrowup" />
          </button>
        </>
      )}
    </div>
  )
}
