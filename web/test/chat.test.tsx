import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, expect, test } from 'vitest'
import type { ChatMessage, Turn } from '../src/api/schemas'
import { actionsCard, conversation, message, ruleCard, turn } from './chatFixtures'
import { rule } from './fixtures'
import { json, renderApp, signedIn } from './render'
import { server } from './server'

beforeEach(() => {
  document.cookie = 'csrf=sess; path=/'
  try {
    localStorage.clear()
  } catch {
    // storage may be unavailable
  }
})
afterEach(() => {
  document.cookie = 'csrf=; max-age=0; path=/'
})

function sse(turns: Turn[]): Response {
  const enc = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const t of turns) c.enqueue(enc.encode(`event: turn\ndata: ${JSON.stringify(t)}\n\n`))
      c.close()
    },
  })
  return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
}

function chatWith(messages: ChatMessage[]) {
  signedIn()
  server.use(
    http.get('/api/chat/conversations', () => json([conversation])),
    http.get('/api/chat/conversations/1/messages', () => json(messages)),
  )
}

test('sends_text_and_renders_streamed_progress_then_answer', async () => {
  chatWith([])
  let sent: unknown
  server.use(
    http.post('/api/chat/conversations/1/messages', async ({ request }) => {
      sent = await request.json()
      return json(turn({ status: 'queued' }, { text: 'quais boletos tenho?' }))
    }),
    http.get('/api/chat/messages/2/stream', () =>
      sse([
        turn({ status: 'running', progress: 'buscando nos e-mails…' }, { text: 'quais boletos tenho?' }),
        turn({ status: 'done', text: 'Achei **2 boletos**.' }, { text: 'quais boletos tenho?' }),
      ]),
    ),
  )
  const user = userEvent.setup()
  renderApp('/chat')
  const box = await screen.findByLabelText('Mensagem')
  await user.type(box, 'quais boletos tenho?{Enter}')
  await waitFor(() => expect(sent).toEqual({ text: 'quais boletos tenho?' }))
  expect(await screen.findByText('2 boletos')).toBeInTheDocument()
  expect(screen.getByText('quais boletos tenho?')).toBeInTheDocument()
})

test('rule_card_confirm_with_existing_suggestions', async () => {
  let body: unknown
  const confirmed = { ...ruleCard, status: 'confirmed' as const, apply_existing: true }
  const card = () => (body === undefined ? ruleCard : confirmed)
  signedIn()
  server.use(
    http.get('/api/chat/conversations', () => json([conversation])),
    http.get('/api/chat/conversations/1/messages', () =>
      json([message({}), message({ id: 2, role: 'assistant', text: 'Entendi assim — confere?', cards: [card()] })]),
    ),
    http.post('/api/chat/cards/11/confirm', async ({ request }) => {
      body = await request.json()
      return json({ ...ruleCard, status: 'confirmed', apply_existing: true })
    }),
  )
  const user = userEvent.setup()
  renderApp('/chat')
  expect(await screen.findByText('lojavertice.com.br')).toBeInTheDocument()
  expect(screen.getByText(/quando tiver documento/)).toBeInTheDocument()
  await user.click(screen.getByLabelText('Gerar sugestões para eles também'))
  await user.click(screen.getByRole('button', { name: 'Confirmar' }))
  await waitFor(() => expect(body).toEqual({ apply_existing: true }))
  expect(await screen.findByText('Confirmada')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Confirmar' })).toBeNull()
})

test('actions_card_reject_all_posts_verdict', async () => {
  chatWith([message({}), message({ id: 2, role: 'assistant', text: 'Preparei.', cards: [actionsCard] })])
  let body: unknown
  server.use(
    http.post('/api/chat/cards/15/confirm', async ({ request }) => {
      body = await request.json()
      return json({ ...actionsCard, status: 'confirmed' })
    }),
  )
  const user = userEvent.setup()
  renderApp('/chat')
  await user.click(await screen.findByRole('button', { name: 'Recusar' }))
  await waitFor(() => expect(body).toEqual({ verdict: 'reject' }))
})

test('failed_turn_retries_and_streams_new_answer', async () => {
  chatWith([message({}), message({ id: 2, role: 'assistant', status: 'failed', error: 'O Gemini está sobrecarregado agora.' })])
  server.use(
    http.post('/api/chat/messages/2/retry', () => json(turn({ id: 3, status: 'queued' }))),
    http.get('/api/chat/messages/3/stream', () => sse([turn({ id: 3, status: 'done', text: 'Agora foi.' })])),
  )
  const user = userEvent.setup()
  renderApp('/chat')
  expect(await screen.findByText('O Gemini está sobrecarregado agora.')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Tentar de novo' }))
  expect(await screen.findByText('Agora foi.')).toBeInTheDocument()
})

test('draft_param_fills_composer', async () => {
  chatWith([])
  renderApp('/chat?draft=Sobre%20a%20sugest%C3%A3o%20%237')
  const box = await screen.findByLabelText('Mensagem')
  await waitFor(() => expect(box).toHaveValue('Sobre a sugestão #7'))
})

test('resumes_a_running_turn_after_mount', async () => {
  chatWith([message({}), message({ id: 2, role: 'assistant', status: 'running', progress: 'pensando…' })])
  server.use(http.get('/api/chat/messages/2/stream', () => sse([turn({ status: 'done', text: 'Pronto, retomei.' })])))
  renderApp('/chat')
  expect(await screen.findByText('Pronto, retomei.')).toBeInTheDocument()
})

test('regras_shows_unless_document', async () => {
  signedIn()
  server.use(
    http.get('/api/agent/rules', () => json([{ ...rule, unless_document: true }])),
    http.get('/api/categories', () => HttpResponse.json({ set_id: 1, version: 1, categories: [], flags: [] })),
    http.get('/api/agent/audit', () => json([])),
  )
  renderApp('/agente?aba=regras')
  expect(await screen.findByText(/exceto com documento/)).toBeInTheDocument()
})

test('confirm_refetches_and_renders_event_note_and_continuation', async () => {
  let fetched = 0
  const before = [message({}), message({ id: 2, role: 'assistant', text: 'Entendi assim — confere?', cards: [ruleCard] })]
  const after = [
    ...before.slice(0, 1),
    message({ id: 2, role: 'assistant', text: 'Entendi assim — confere?', cards: [{ ...ruleCard, status: 'confirmed' }] }),
    message({ id: 3, role: 'user', input: 'event', text: "[evento] O usuário confirmou o cartão #11: regra 'lixo' criada (#3)." }),
    message({ id: 4, role: 'assistant', status: 'queued', text: '' }),
  ]
  signedIn()
  server.use(
    http.get('/api/chat/conversations', () => json([conversation])),
    http.get('/api/chat/conversations/1/messages', () => json(fetched++ === 0 ? before : after)),
    http.post('/api/chat/cards/11/confirm', () => json({ ...ruleCard, status: 'confirmed' })),
    http.get('/api/chat/messages/4/stream', () =>
      sse([turn({ id: 4, status: 'done', text: 'Regra criada; agora apago os 5.' }, { id: 3, input: 'event', text: "[evento] O usuário confirmou o cartão #11: regra 'lixo' criada (#3)." })]),
    ),
  )
  const user = userEvent.setup()
  renderApp('/chat')
  await user.click(await screen.findByRole('button', { name: 'Confirmar' }))
  const note = await screen.findByRole('note')
  expect(note).toHaveTextContent("O usuário confirmou o cartão #11: regra 'lixo' criada (#3).")
  expect(note).not.toHaveTextContent('[evento]')
  expect(await screen.findByText('Regra criada; agora apago os 5.')).toBeInTheDocument()
})
