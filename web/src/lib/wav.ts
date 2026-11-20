// 16-bit PCM mono WAV from the Float32 frames the recorder collects.
// WAV plays and uploads everywhere (iOS Safari included) and the server
// checks the same format (src/chat/audio.py).

export const WAV_RATE = 16000

/** Linear-interpolated resample of every chunk to `outputRate`, clamped to [-1, 1]. */
export function encodeWav(chunks: Float32Array[], inputRate: number, outputRate = WAV_RATE): Blob {
  const total = chunks.reduce((n, c) => n + c.length, 0)
  const input = new Float32Array(total)
  let at = 0
  for (const c of chunks) {
    input.set(c, at)
    at += c.length
  }
  const ratio = inputRate / outputRate
  const frames = Math.floor(total / ratio)
  const buffer = new ArrayBuffer(44 + frames * 2)
  const v = new DataView(buffer)
  const ascii = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(offset + i, s.charCodeAt(i))
  }
  ascii(0, 'RIFF')
  v.setUint32(4, 36 + frames * 2, true)
  ascii(8, 'WAVE')
  ascii(12, 'fmt ')
  v.setUint32(16, 16, true)
  v.setUint16(20, 1, true) // PCM
  v.setUint16(22, 1, true) // mono
  v.setUint32(24, outputRate, true)
  v.setUint32(28, outputRate * 2, true)
  v.setUint16(32, 2, true)
  v.setUint16(34, 16, true)
  ascii(36, 'data')
  v.setUint32(40, frames * 2, true)
  for (let i = 0; i < frames; i++) {
    const pos = i * ratio
    const lo = Math.floor(pos)
    const hi = Math.min(lo + 1, total - 1)
    const a = input[lo] ?? 0
    const sample = a + ((input[hi] ?? a) - a) * (pos - lo)
    const clamped = Math.max(-1, Math.min(1, sample))
    v.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true)
  }
  return new Blob([buffer], { type: 'audio/wav' })
}
