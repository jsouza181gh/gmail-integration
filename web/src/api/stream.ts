// Follows one chat turn over SSE (GET /api/chat/messages/:id/stream).
// fetch + ReadableStream instead of EventSource, so the 401 -> refresh path is
// the same as every other call.
import { authedFetch } from './client'
import { ApiError, BAD_RESPONSE, fallbackDetail } from './errors'
import { Turn } from './schemas'

const FINAL = new Set(['done', 'failed'])

/** Calls `onTurn` with every snapshot; 'done' after a final one, 'closed' if the
 * server ended the stream first (it caps each stream; the caller re-opens). */
export async function streamTurn(
  messageId: number,
  onTurn: (t: Turn) => void,
  signal: AbortSignal,
): Promise<'done' | 'closed'> {
  const res = await authedFetch(`/api/chat/messages/${messageId}/stream`, { signal })
  if (!res.ok || !res.body) throw new ApiError(res.status, fallbackDetail(res.status))
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return 'closed'
    buffer += value.replaceAll('\r\n', '\n')
    let cut = buffer.indexOf('\n\n')
    while (cut >= 0) {
      const frame = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      const turn = parseFrame(frame)
      if (turn) {
        onTurn(turn)
        if (FINAL.has(turn.assistant.status)) {
          await reader.cancel().catch(() => {})
          return 'done'
        }
      }
      cut = buffer.indexOf('\n\n')
    }
  }
}

function parseFrame(frame: string): Turn | null {
  let event = 'message'
  const data: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith(':')) continue
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (event !== 'turn' || data.length === 0) return null
  let json: unknown
  try {
    json = JSON.parse(data.join('\n'))
  } catch {
    throw new ApiError(0, BAD_RESPONSE)
  }
  const parsed = Turn.safeParse(json)
  if (!parsed.success) throw new ApiError(0, BAD_RESPONSE)
  return parsed.data
}
