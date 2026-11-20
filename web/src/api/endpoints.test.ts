import { http, HttpResponse } from 'msw'
import { expect, test } from 'vitest'
import * as cf from '../../test/chatFixtures'
import * as fx from '../../test/fixtures'
import { server } from '../../test/server'
import * as api from './endpoints'

test('listEmails_forwards_filters_as_query', async () => {
  let url = ''
  server.use(
    http.get('/api/emails', ({ request }) => {
      url = request.url
      return HttpResponse.json(fx.emailPage)
    }),
  )
  const page = await api.listEmails({ category: 'Finanças', relevant: true, before: 'c1' })
  expect(page.items).toHaveLength(2)
  const q = new URL(url).searchParams
  expect([q.get('category'), q.get('relevant'), q.get('before')]).toEqual(['Finanças', 'true', 'c1'])
})

test('approve_sends_ids_and_omits_empty_comment', async () => {
  let body: unknown
  server.use(
    http.post('/api/actions/approve', async ({ request }) => {
      body = await request.json()
      return HttpResponse.json({ decided: 2 })
    }),
  )
  const out = await api.approve([1, 2])
  expect(out.decided).toBe(2)
  expect(body).toEqual({ ids: [1, 2] })
})

test('documentLink_returns_url', async () => {
  server.use(
    http.post('/api/documents/7/link', () =>
      HttpResponse.json({ url: '/api/documents/7/download?t=x', expires_in: 60 }),
    ),
  )
  expect((await api.documentLink(7)).url).toBe('/api/documents/7/download?t=x')
})

test('setPolicy_puts_target_trigger_policy', async () => {
  let body: unknown
  server.use(
    http.put('/api/agent/policies', async ({ request }) => {
      body = await request.json()
      return new HttpResponse(null, { status: 204 })
    }),
  )
  await api.setPolicy('archive', 'webhook', 'auto')
  expect(body).toEqual({ target: 'archive', trigger: 'webhook', policy: 'auto' })
})


test('sendAudio_posts_raw_wav', async () => {
  let type: string | null = null
  let size = 0
  server.use(
    http.post('/api/chat/conversations/3/audio', async ({ request }) => {
      type = request.headers.get('content-type')
      size = (await request.arrayBuffer()).byteLength
      return HttpResponse.json(cf.turn({}, { input: 'audio', text: '' }))
    }),
  )
  const out = await api.sendAudio(3, new Blob([new Uint8Array(100)], { type: 'audio/wav' }))
  expect(type).toBe('audio/wav')
  expect(size).toBe(100)
  expect(out.user.input).toBe('audio')
})

test('confirmCard_sends_options', async () => {
  let body: unknown
  server.use(
    http.post('/api/chat/cards/11/confirm', async ({ request }) => {
      body = await request.json()
      return HttpResponse.json({ ...cf.ruleCard, status: 'confirmed' })
    }),
  )
  expect((await api.confirmCard(11, { apply_existing: true })).status).toBe('confirmed')
  expect(body).toEqual({ apply_existing: true })
})
