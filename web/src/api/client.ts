import { z } from 'zod'
import { ApiError, BAD_RESPONSE, OFFLINE, RATE_LIMITED, fallbackDetail } from './errors'

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE'
type QueryValue = string | number | boolean | undefined

interface Options<S extends z.ZodType | undefined> {
  body?: unknown
  /** Sent as-is (e.g. an audio Blob) with `contentType`; takes precedence over `body`. */
  raw?: BodyInit
  contentType?: string
  query?: Record<string, QueryValue>
  schema?: S
}

export interface FetchInit {
  method?: Method
  body?: BodyInit
  contentType?: string
  query?: Record<string, QueryValue>
  signal?: AbortSignal
}

const NO_REFRESH = new Set(['/api/auth/login', '/api/auth/2fa', '/api/auth/refresh'])
const WRITES = new Set<Method>(['POST', 'PUT', 'DELETE'])

let sessionLost: () => void = () => {}
let noAccount: () => void = () => {}
let refreshing: Promise<boolean> | null = null

export function onSessionLost(cb: () => void): void {
  sessionLost = cb
}

/** The user is logged in but has no Gmail account yet (server answers 409 `no_account`). */
export function onNoAccount(cb: () => void): void {
  noAccount = cb
}

function csrfToken(): string | null {
  for (const part of document.cookie.split('; ')) {
    const [name, ...rest] = part.split('=')
    if (name === 'csrf') return decodeURIComponent(rest.join('='))
  }
  return null
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = new URL(path, window.location.origin)
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value))
  }
  return url.toString()
}

async function send(path: string, init: FetchInit): Promise<Response> {
  const method = init.method ?? 'GET'
  const headers: Record<string, string> = {}
  if (init.contentType) headers['Content-Type'] = init.contentType
  if (WRITES.has(method)) {
    const token = csrfToken()
    if (token) headers['X-CSRF-Token'] = token
  }
  try {
    return await fetch(buildUrl(path, init.query), {
      method,
      headers,
      credentials: 'same-origin',
      body: init.body,
      signal: init.signal,
    })
  } catch (err) {
    if (init.signal?.aborted) throw err
    throw new ApiError(0, OFFLINE)
  }
}

// One refresh at a time: the server rotates the refresh token on every use,
// and reusing an old one revokes the whole session family.
function refreshOnce(): Promise<boolean> {
  refreshing ??= send('/api/auth/refresh', { method: 'POST' })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

async function errorFrom(res: Response): Promise<ApiError> {
  if (res.status === 429) return new ApiError(429, RATE_LIMITED)
  const body: unknown = await res.json().catch(() => null)
  if (res.status === 409 && z.object({ code: z.literal('no_account') }).safeParse(body).success) noAccount()
  const parsed = z.object({ detail: z.string() }).safeParse(body)
  return new ApiError(res.status, parsed.success ? parsed.data.detail : fallbackDetail(res.status))
}

/** fetch with CSRF on writes and one refresh-and-retry on 401 (what `request` builds on). */
export async function authedFetch(path: string, init: FetchInit = {}): Promise<Response> {
  let res = await send(path, init)
  if (res.status === 401 && !NO_REFRESH.has(path)) {
    // No csrf cookie = never signed in (or already signed out): a refresh would only
    // be refused by the server and logged as a CSRF security event.
    if (csrfToken() !== null && (await refreshOnce())) {
      res = await send(path, init)
    } else {
      sessionLost()
    }
  }
  return res
}

export async function request<S extends z.ZodType | undefined = undefined>(
  method: Method,
  path: string,
  opts: Options<S> = {},
): Promise<S extends z.ZodType ? z.infer<S> : void> {
  const json = opts.body !== undefined
  const res = await authedFetch(path, {
    method,
    query: opts.query,
    body: opts.raw ?? (json ? JSON.stringify(opts.body) : undefined),
    contentType: opts.raw !== undefined ? opts.contentType : json ? 'application/json' : undefined,
  })
  if (!res.ok) throw await errorFrom(res)
  if (!opts.schema) return undefined as S extends z.ZodType ? z.infer<S> : void
  const parsed = opts.schema.safeParse(await res.json().catch(() => null))
  if (!parsed.success) throw new ApiError(0, BAD_RESPONSE)
  return parsed.data as S extends z.ZodType ? z.infer<S> : void
}
