import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, expect, test } from 'vitest'
import { turn } from '../../test/chatFixtures'
import { server } from '../../test/server'
import type { Turn } from './schemas'
import { streamTurn } from './stream'

beforeEach(() => {
  document.cookie = 'csrf=sess; path=/'
})
afterEach(() => {
  document.cookie = 'csrf=; max-age=0; path=/'
})

function sse(chunks: string[]): Response {
  const enc = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const chunk of chunks) c.enqueue(enc.encode(chunk))
      c.close()
    },
  })
  return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
}

function frame(t: Turn): string {
  return `event: turn\ndata: ${JSON.stringify(t)}\n\n`
}

test('parses_split_frames_and_resolves_done', async () => {
  const running = frame(turn({ status: 'running', progress: 'buscando nos e-mails…' }))
  const done = frame(turn({ status: 'done', text: 'Achei.' }))
  const all = `${running}: ping\n\n${done}`
  server.use(
    http.get('/api/chat/messages/2/stream', () => sse([all.slice(0, 17), all.slice(17, 90), all.slice(90)])),
  )
  const seen: Turn[] = []
  const out = await streamTurn(2, (t) => seen.push(t), new AbortController().signal)
  expect(out).toBe('done')
  expect(seen.map((t) => t.assistant.status)).toEqual(['running', 'done'])
  expect(seen[0]?.assistant.progress).toBe('buscando nos e-mails…')
})

test('stream_closed_before_final_frame_reports_closed', async () => {
  server.use(http.get('/api/chat/messages/2/stream', () => sse([frame(turn({ status: 'running' }))])))
  const out = await streamTurn(2, () => {}, new AbortController().signal)
  expect(out).toBe('closed')
})

test('refreshes_once_on_401_then_streams', async () => {
  let refreshed = false
  server.use(
    http.get('/api/chat/messages/2/stream', () =>
      refreshed ? sse([frame(turn({ status: 'failed', error: 'x' }))]) : new HttpResponse(null, { status: 401 }),
    ),
    http.post('/api/auth/refresh', () => {
      refreshed = true
      return new HttpResponse(null, { status: 204 })
    }),
  )
  const seen: Turn[] = []
  expect(await streamTurn(2, (t) => seen.push(t), new AbortController().signal)).toBe('done')
  expect(seen[0]?.assistant.status).toBe('failed')
})
