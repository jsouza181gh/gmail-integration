import { z } from 'zod'
import { ApiError, BAD_RESPONSE, OFFLINE, RATE_LIMITED, fallbackDetail } from './errors'

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE'
type QueryValue = string | number | boolean | undefined

interface Options<S extends z.ZodType | undefined> {
  body?: unknown
  query?: Record<string, QueryValue>
  schema?: S
}

const NO_REFRESH = new Set(['/api/auth/login', '/api/auth/2fa', '/api/auth/refresh'])
const WRITES = new Set<Method>(['POST', 'PUT', 'DELETE'])

let sessionLost: () => void = () => {}
let refreshing: Promise<boolean> | null = null

export function onSessionLost(cb: () => void): void {
  sessionLost = cb
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

async function send(method: Method, path: string, opts: Options<z.ZodType | undefined>): Promise<Response> {
  const headers: Record<string, string> = {}
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (WRITES.has(method)) {
    const token = csrfToken()
    if (token) headers['X-CSRF-Token'] = token
  }
  try {
    return await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      credentials: 'same-origin',
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    })
  } catch {
    throw new ApiError(0, OFFLINE)
  }
}

// One refresh at a time: the server rotates the refresh token on every use,
// and reusing an old one revokes the whole session family.
function refreshOnce(): Promise<boolean> {
  refreshing ??= send('POST', '/api/auth/refresh', {})
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

async function errorFrom(res: Response): Promise<ApiError> {
  if (res.status === 429) return new ApiError(429, RATE_LIMITED)
  const parsed = z.object({ detail: z.string() }).safeParse(await res.json().catch(() => null))
  return new ApiError(res.status, parsed.success ? parsed.data.detail : fallbackDetail(res.status))
}

export async function request<S extends z.ZodType | undefined = undefined>(
  method: Method,
  path: string,
  opts: Options<S> = {},
): Promise<S extends z.ZodType ? z.infer<S> : void> {
  let res = await send(method, path, opts)
  if (res.status === 401 && !NO_REFRESH.has(path)) {
    // No csrf cookie = never signed in (or already signed out): a refresh would only
    // be refused by the server and logged as a CSRF security event.
    if (csrfToken() !== null && (await refreshOnce())) {
      res = await send(method, path, opts)
    } else {
      sessionLost()
    }
  }
  if (!res.ok) throw await errorFrom(res)
  if (!opts.schema) return undefined as S extends z.ZodType ? z.infer<S> : void
  const parsed = opts.schema.safeParse(await res.json().catch(() => null))
  if (!parsed.success) throw new ApiError(0, BAD_RESPONSE)
  return parsed.data as S extends z.ZodType ? z.infer<S> : void
}
