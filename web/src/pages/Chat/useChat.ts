// State of the chat page: conversations, the current one's messages (kept in
// the query cache) and one SSE stream per assistant message still in flight.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from '../../api/endpoints'
import type { ChatCard, ChatMessage, Turn } from '../../api/schemas'
import { streamTurn } from '../../api/stream'

const LAST_KEY = 'chat.conversation'
const LIVE = new Set(['queued', 'running'])

function readLast(): number | null {
  try {
    const raw = localStorage.getItem(LAST_KEY)
    return raw ? Number(raw) || null : null
  } catch {
    return null
  }
}

function writeLast(id: number): void {
  try {
    localStorage.setItem(LAST_KEY, String(id))
  } catch {
    // per-viewer convenience only
  }
}

const messagesKey = (id: number | null) => ['chat', 'messages', id] as const
const conversationsKey = ['chat', 'conversations'] as const

function upsert(list: ChatMessage[] | undefined, ...msgs: ChatMessage[]): ChatMessage[] {
  const out = [...(list ?? [])]
  for (const m of msgs) {
    const at = out.findIndex((x) => x.id === m.id)
    if (at >= 0) out[at] = m
    else out.push(m)
  }
  return out.sort((a, b) => a.id - b.id)
}

export function useChat() {
  const qc = useQueryClient()
  const [chosen, setCurrent] = useState<number | null>(readLast)
  const convs = useQuery({ queryKey: conversationsKey, queryFn: () => api.conversations() })
  // Nothing remembered (or it vanished): open the most recent conversation.
  const known = convs.data?.some((c) => c.id === chosen) ?? false
  const current = known ? chosen : (convs.data?.[0]?.id ?? (convs.data ? null : chosen))

  const msgs = useQuery({
    queryKey: messagesKey(current),
    queryFn: () => api.chatMessages(current as number),
    enabled: current !== null,
  })
  const messages = useMemo(() => (current === null ? [] : (msgs.data ?? [])), [current, msgs.data])

  const merge = useCallback(
    (conversationId: number, t: Turn) => {
      qc.setQueryData<ChatMessage[]>(messagesKey(conversationId), (old) => upsert(old, t.user, t.assistant))
    },
    [qc],
  )

  // One stream per live assistant message; a stream the server closed early is
  // re-opened after reloading the conversation.
  const streams = useRef(new Map<number, AbortController>())
  useEffect(() => {
    if (current === null) return
    for (const m of messages) {
      if (m.role !== 'assistant' || !LIVE.has(m.status) || streams.current.has(m.id)) continue
      const ctl = new AbortController()
      streams.current.set(m.id, ctl)
      const conversationId = current
      streamTurn(m.id, (t) => merge(conversationId, t), ctl.signal)
        .then(async (outcome) => {
          streams.current.delete(m.id)
          if (outcome === 'closed') await qc.invalidateQueries({ queryKey: messagesKey(conversationId) })
          else await qc.invalidateQueries({ queryKey: conversationsKey })
        })
        .catch(() => {
          streams.current.delete(m.id)
        })
    }
  }, [messages, current, merge, qc])

  useEffect(() => {
    const live = streams.current
    return () => {
      for (const ctl of live.values()) ctl.abort()
      live.clear()
    }
  }, [current])

  const ensureConversation = useCallback(async (): Promise<number> => {
    if (current !== null) return current
    const conv = await api.newConversation()
    writeLast(conv.id)
    setCurrent(conv.id)
    await qc.invalidateQueries({ queryKey: conversationsKey })
    return conv.id
  }, [current, qc])

  const send = useCallback(
    async (text: string) => {
      const id = await ensureConversation()
      merge(id, await api.sendText(id, text))
    },
    [ensureConversation, merge],
  )

  const sendAudio = useCallback(
    async (wav: Blob) => {
      const id = await ensureConversation()
      merge(id, await api.sendAudio(id, wav))
    },
    [ensureConversation, merge],
  )

  const retry = useCallback(
    async (messageId: number) => {
      if (current !== null) merge(current, await api.retryTurn(messageId))
    },
    [current, merge],
  )

  const replaceCard = useCallback(
    (card: ChatCard) => {
      qc.setQueryData<ChatMessage[]>(messagesKey(current), (old) =>
        (old ?? []).map((m) =>
          m.cards.some((c) => c.id === card.id)
            ? { ...m, cards: m.cards.map((c) => (c.id === card.id ? card : c)) }
            : m,
        ),
      )
    },
    [current, qc],
  )

  // Confirming may start a continuation turn on the server: refetch so its
  // event note shows and its live answer gets a stream.
  const confirm = useCallback(
    async (cardId: number, opts: api.ConfirmOptions = {}) => {
      replaceCard(await api.confirmCard(cardId, opts))
      if (current !== null) await qc.invalidateQueries({ queryKey: messagesKey(current) })
    },
    [replaceCard, current, qc],
  )
  const cancel = useCallback(async (cardId: number) => replaceCard(await api.cancelCard(cardId)), [replaceCard])

  const newConversation = useCallback(async () => {
    const conv = await api.newConversation()
    writeLast(conv.id)
    setCurrent(conv.id)
    await qc.invalidateQueries({ queryKey: conversationsKey })
  }, [qc])

  const select = useCallback((id: number) => {
    writeLast(id)
    setCurrent(id)
  }, [])

  return {
    conversations: convs.data ?? [],
    current,
    messages,
    loading: msgs.isLoading && current !== null,
    loadError: msgs.isError,
    busy: messages.some((m) => m.role === 'assistant' && LIVE.has(m.status)),
    send,
    sendAudio,
    retry,
    confirm,
    cancel,
    newConversation,
    select,
  }
}
