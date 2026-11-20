import { expect, test } from 'vitest'
import { encodeWav } from './wav'

async function bytes(blob: Blob): Promise<DataView> {
  return new DataView(await blob.arrayBuffer())
}

function ascii(v: DataView, at: number, n: number): string {
  return String.fromCharCode(...Array.from({ length: n }, (_, i) => v.getUint8(at + i)))
}

test('encodeWav_writes_16k_mono_pcm16_header', async () => {
  const second = new Float32Array(48000).fill(0.5)
  const blob = encodeWav([second], 48000)
  expect(blob.type).toBe('audio/wav')
  const v = await bytes(blob)
  expect(ascii(v, 0, 4)).toBe('RIFF')
  expect(ascii(v, 8, 4)).toBe('WAVE')
  expect(v.getUint16(20, true)).toBe(1) // PCM
  expect(v.getUint16(22, true)).toBe(1) // mono
  expect(v.getUint32(24, true)).toBe(16000)
  expect(v.getUint16(34, true)).toBe(16)
  expect(ascii(v, 36, 4)).toBe('data')
  expect(v.getUint32(40, true)).toBe(16000 * 2)
  expect(v.byteLength).toBe(44 + 16000 * 2)
  expect(v.getUint32(4, true)).toBe(v.byteLength - 8)
})

test('encodeWav_downsamples_across_chunks_and_clamps', async () => {
  const a = new Float32Array(24000).fill(2) // clipped to +1
  const b = new Float32Array(24000).fill(-2) // clipped to -1
  const v = await bytes(encodeWav([a, b], 48000))
  expect(v.getUint32(40, true)).toBe(16000 * 2)
  expect(v.getInt16(44, true)).toBe(32767)
  expect(v.getInt16(v.byteLength - 2, true)).toBe(-32768)
})

test('encodeWav_keeps_rate_when_already_16k', async () => {
  const v = await bytes(encodeWav([new Float32Array(1600)], 16000))
  expect(v.getUint32(40, true)).toBe(3200)
})
