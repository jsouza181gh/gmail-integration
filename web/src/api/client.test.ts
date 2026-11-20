import { delay, http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { z } from 'zod'
import { server } from '../../test/server'
import { ApiError } from './errors'
import { onSessionLost, request } from './client'

const Item = z.object({ n: z.number() })

// A signed-in browser always holds the csrf cookie (it lives as long as the refresh token).
beforeEach(() => {
  document.cookie = 'csrf=sess; path=/'
})
afterEach(() => {
  document.cookie = 'csrf=; max-age=0; path=/'
})

describe('request', () => {
  test('post_sends_csrf_header_from_cookie', async () => {
    document.cookie = 'csrf=abc; path=/'
    const seen: Array<string | null> = []
    server.use(
      http.post('/api/x', ({ request: r }) => {
        seen.push(r.headers.get('x-csrf-token'))
        return new HttpResponse(null, { status: 204 })
      }),
      http.get('/api/x', ({ request: r }) => {
        seen.push(r.headers.get('x-csrf-token'))
        return HttpResponse.json({ n: 1 })
      }),
    )
    await request('POST', '/api/x', { body: { a: 1 } })
    await request('GET', '/api/x', { schema: Item })
    expect(seen).toEqual(['abc', null])
  })

  test('401_triggers_single_refresh_then_retries', async () => {
    let refreshed = false
    let refreshCalls = 0
    server.use(
      http.get('/api/emails/relevant', () =>
        refreshed ? HttpResponse.json({ n: 2 }) : new HttpResponse(null, { status: 401 }),
      ),
      http.post('/api/auth/refresh', async () => {
        refreshCalls += 1
        await delay(30)
        refreshed = true
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const results = await Promise.all([
      request('GET', '/api/emails/relevant', { schema: Item }),
      request('GET', '/api/emails/relevant', { schema: Item }),
      request('GET', '/api/emails/relevant', { schema: Item }),
    ])
    expect(refreshCalls).toBe(1)
    expect(results).toEqual([{ n: 2 }, { n: 2 }, { n: 2 }])
  })

  test('failed_refresh_calls_session_lost', async () => {
    const lost = vi.fn()
    onSessionLost(lost)
    server.use(
      http.get('/api/emails/relevant', () => new HttpResponse(null, { status: 401 })),
      http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
    )
    await expect(request('GET', '/api/emails/relevant', { schema: Item })).rejects.toMatchObject({
      status: 401,
    })
    expect(lost).toHaveBeenCalledTimes(1)
  })

  test('schema_mismatch_rejects_with_portuguese_detail', async () => {
    server.use(http.get('/api/x', () => HttpResponse.json({ n: 'nope' })))
    const err = await request('GET', '/api/x', { schema: Item }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 0, detail: 'resposta inesperada do servidor' })
  })

  test('error_detail_taken_from_json_and_429_has_fixed_text', async () => {
    server.use(
      http.post('/api/a', () => HttpResponse.json({ detail: 'requisição recusada' }, { status: 403 })),
      http.post('/api/b', () => HttpResponse.json({ detail: 'muitas requisições' }, { status: 429 })),
    )
    await expect(request('POST', '/api/a')).rejects.toMatchObject({
      status: 403,
      detail: 'requisição recusada',
    })
    await expect(request('POST', '/api/b')).rejects.toMatchObject({
      status: 429,
      detail: 'Muitas requisições, tente de novo em instantes',
    })
  })

  test('401_without_a_session_skips_refresh_and_reports_session_lost', async () => {
    document.cookie = 'csrf=; max-age=0; path=/'
    const lost = vi.fn()
    onSessionLost(lost)
    let refreshCalls = 0
    server.use(
      http.get('/api/auth/me', () => new HttpResponse(null, { status: 401 })),
      http.post('/api/auth/refresh', () => {
        refreshCalls += 1
        return new HttpResponse(null, { status: 403 })
      }),
    )
    await expect(request('GET', '/api/auth/me', { schema: Item })).rejects.toMatchObject({ status: 401 })
    expect(refreshCalls).toBe(0)
    expect(lost).toHaveBeenCalledTimes(1)
  })

  test('auth_routes_never_trigger_refresh', async () => {
    let refreshCalls = 0
    server.use(
      http.post('/api/auth/login', () => HttpResponse.json({ detail: 'credenciais inválidas' }, { status: 401 })),
      http.post('/api/auth/refresh', () => {
        refreshCalls += 1
        return new HttpResponse(null, { status: 204 })
      }),
    )
    await expect(request('POST', '/api/auth/login', { body: {} })).rejects.toMatchObject({
      status: 401,
      detail: 'credenciais inválidas',
    })
    expect(refreshCalls).toBe(0)
  })

  test('query_params_skip_undefined', async () => {
    let url = ''
    server.use(
      http.get('/api/emails', ({ request: r }) => {
        url = r.url
        return HttpResponse.json({ n: 1 })
      }),
    )
    await request('GET', '/api/emails', { query: { q: 'a b', relevant: true, before: undefined, limit: 50 }, schema: Item })
    const u = new URL(url)
    expect(u.searchParams.get('q')).toBe('a b')
    expect(u.searchParams.get('relevant')).toBe('true')
    expect(u.searchParams.get('limit')).toBe('50')
    expect(u.searchParams.has('before')).toBe(false)
  })
})
