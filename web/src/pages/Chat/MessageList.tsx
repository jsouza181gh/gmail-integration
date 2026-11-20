import { useEffect, useRef, useState } from 'react'
import { messageAudioUrl } from '../../api/endpoints'
import type { ChatMessage } from '../../api/schemas'
import { renderMarkdown } from '../../lib/markdown'
import { Icon } from '../../ui/Icon'
import { CardView, type CardHandlers } from './cards'
import { duration } from './Composer'

function UserBubble({ m }: { m: ChatMessage }) {
  const [play, setPlay] = useState(false)
  return (
    <div className="chat-user t-body">
      {m.input === 'audio' ? (
        <span className="row t-small ia-subtle chat-user__audio">
          <Icon name="mic" />
          {`áudio, ${duration(m.audio_ms ?? 0)}`}
          {play ? null : (
            <button type="button" className="chat-link" onClick={() => setPlay(true)}>
              <Icon name="play" />
              Ouvir
            </button>
          )}
        </span>
      ) : null}
      {play ? <audio controls autoPlay src={messageAudioUrl(m.id)} className="chat-audio" /> : null}
      {m.text ? <span className="chat-user__text">{m.text}</span> : null}
      {!m.text && m.input === 'audio' ? <span className="ia-muted">transcrevendo…</span> : null}
    </div>
  )
}

function AssistantMessage({ m, h, onRetry }: { m: ChatMessage; h: CardHandlers; onRetry: (id: number) => void }) {
  const live = m.status === 'queued' || m.status === 'running'
  return (
    <div className="chat-bot">
      <span className="chat-bot__avatar" aria-hidden="true">
        <Icon name="bot" />
      </span>
      <div className="chat-bot__body">
        {m.text ? <div className="t-body chat-md">{renderMarkdown(m.text)}</div> : null}
        {live ? (
          <p className="t-small ia-muted chat-progress" role="status">
            <span className="chat-spinner" aria-hidden="true" />
            {m.progress ?? 'na fila…'}
          </p>
        ) : null}
        {m.status === 'failed' ? (
          <div className="toolbar">
            <p className="warn-line" role="alert" style={{ margin: 0 }}>
              <span className="ia-ico-warning">
                <Icon name="alert" />
              </span>
              {m.error ?? 'Não consegui responder.'}
            </p>
            <button type="button" className="ia-btn ia-btn--sm" onClick={() => onRetry(m.id)}>
              <Icon name="refresh" />
              Tentar de novo
            </button>
          </div>
        ) : null}
        {m.cards.map((c) => (
          <CardView key={c.id} card={c} h={h} />
        ))}
      </div>
    </div>
  )
}

export function MessageList({
  messages,
  h,
  onRetry,
}: {
  messages: ChatMessage[]
  h: CardHandlers
  onRetry: (id: number) => void
}) {
  const end = useRef<HTMLDivElement>(null)
  const last = messages.at(-1)
  const signature = `${messages.length}:${last?.status}:${last?.text.length}:${last?.cards.length}`
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end' })
  }, [signature])
  return (
    <div className="chat-thread" aria-live="polite">
      {messages.map((m) =>
        m.role === 'user' ? (
          <UserBubble key={m.id} m={m} />
        ) : (
          <AssistantMessage key={m.id} m={m} h={h} onRetry={onRetry} />
        ),
      )}
      <div ref={end} />
    </div>
  )
}
