import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import * as api from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import type { ChatMessage } from '../../api/schemas'
import { usePendingCount } from '../../api/hooks'
import { formatWhen } from '../../lib/format'
import { Empty } from '../../ui/Empty'
import { Icon } from '../../ui/Icon'
import { useToast } from '../../ui/Toast'
import type { CardHandlers } from './cards'
import { Composer } from './Composer'
import { MessageList } from './MessageList'
import { useChat } from './useChat'

function why(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback
}

const MODEL_KEY = 'chat.model'

function readModel(): string | null {
  try {
    return localStorage.getItem(MODEL_KEY)
  } catch {
    return null
  }
}

function writeModel(model: string | null) {
  try {
    if (model) localStorage.setItem(MODEL_KEY, model)
    else localStorage.removeItem(MODEL_KEY)
  } catch {
    // storage may be unavailable
  }
}

const LIVE = new Set(['queued', 'running'])

/** Toasts each model call that failed while an answer was in flight, and the
 * answer's own failure; history loaded with the page stays quiet. */
function useFailureToasts(messages: ChatMessage[], show: (text: string) => void) {
  const watched = useRef(new Map<number, number>())
  useEffect(() => {
    for (const m of messages) {
      if (m.role !== 'assistant') continue
      const seen = watched.current.get(m.id)
      if (seen === undefined) {
        if (LIVE.has(m.status)) watched.current.set(m.id, m.failures.length)
        continue
      }
      for (const f of m.failures.slice(seen)) show(`${f.model}: ${f.reason}`)
      watched.current.set(m.id, m.failures.length)
      if (!LIVE.has(m.status)) {
        watched.current.delete(m.id)
        if (m.status === 'failed' && m.error) show(m.error)
      }
    }
  }, [messages, show])
}

export default function Chat() {
  const chat = useChat()
  const toast = useToast()
  const models = useQuery({ queryKey: ['chat', 'models'], queryFn: () => api.chatModels() })
  const [model, setModel] = useState<string | null>(readModel)
  const chosen = model && models.data?.models.includes(model) ? model : null
  const pickModel = (m: string | null) => {
    writeModel(m)
    setModel(m)
  }
  useFailureToasts(chat.messages, toast.show)
  const pending = usePendingCount()
  const [params, setParams] = useSearchParams()
  // 'Discutir no chat' (Aprovações) arrives as ?draft=...
  const [text, setText] = useState(() => params.get('draft') ?? '')
  const [now] = useState(() => new Date())

  const hasDraft = params.has('draft')
  useEffect(() => {
    if (hasDraft) setParams({}, { replace: true })
  }, [hasDraft, setParams])

  const attempt = async (fn: () => Promise<void>, fallback: string): Promise<boolean> => {
    try {
      await fn()
      return true
    } catch (err) {
      toast.show(why(err, fallback))
      return false
    }
  }

  const h: CardHandlers = {
    onConfirm: async (id, opts) => {
      await attempt(() => chat.confirm(id, opts), 'Não foi possível confirmar.')
    },
    onCancel: async (id) => {
      await attempt(() => chat.cancel(id), 'Não foi possível cancelar.')
    },
    onAdjust: async (id) => {
      if (await attempt(() => chat.cancel(id), 'Não foi possível cancelar.')) setText('Ajuste: ')
    },
  }

  return (
    <div className="chat-page">
      <header className="chat-head">
        <h1 className="h-heading">Chat</h1>
        <span className="toolbar">
          {chat.conversations.length > 1 ? (
            <label className="ia-input ia-input--dark" style={{ height: 32 }}>
              <select
                aria-label="Conversa"
                value={chat.current ?? ''}
                onChange={(e) => chat.select(Number(e.target.value))}
              >
                {chat.conversations.map((c) => (
                  <option key={c.id} value={c.id}>
                    {`${c.title ?? 'Sem título'} · ${formatWhen(c.updated_at, now)}`}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <button
            type="button"
            className="ia-btn ia-btn--sm"
            onClick={() => void attempt(chat.newConversation, 'Não foi possível abrir uma conversa.')}
          >
            <Icon name="plus" />
            Nova conversa
          </button>
        </span>
      </header>

      <main className="chat-main">
        {chat.loadError ? <Empty text="Não foi possível carregar a conversa." /> : null}
        {!chat.loading && !chat.loadError && chat.messages.length === 0 ? (
          <Empty text="Pergunte sobre seus e-mails e documentos, peça o que tem para aprovar ou dite uma regra." />
        ) : null}
        <MessageList
          messages={chat.messages}
          h={h}
          onRetry={(id) => void attempt(() => chat.retry(id), 'Não foi possível tentar de novo.')}
        />
      </main>

      <footer className="chat-foot">
        <Composer
          text={text}
          onText={setText}
          busy={chat.busy}
          onSend={(t) => attempt(() => chat.send(t, chosen), 'Não foi possível enviar.')}
          onAudio={(wav) => attempt(() => chat.sendAudio(wav, chosen), 'Não foi possível enviar o áudio.')}
          onError={(m) => toast.show(m)}
          models={models.data?.models ?? []}
          model={chosen}
          onModel={pickModel}
        />
        <p className="t-small ia-subtle chat-foot__note">
          Mudanças de regra e de configuração sempre pedem confirmação.
          {pending > 0 ? (
            <>
              {' Você também tem '}
              <Link to="/aprovacoes">{pending === 1 ? '1 aprovação pendente' : `${pending} aprovações pendentes`}</Link>.
            </>
          ) : null}
        </p>
      </footer>
    </div>
  )
}
