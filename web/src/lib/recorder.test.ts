import { afterEach, expect, test, vi } from 'vitest'
import { startRecording } from './recorder'

const order: string[] = []

class FakeContext {
  sampleRate = 16000
  audioWorklet = { addModule: async () => void order.push('worklet') }
  constructor() {
    order.push('context')
  }
  resume() {
    order.push('resume')
    return Promise.resolve()
  }
  createMediaStreamSource() {
    return { connect: () => {}, disconnect: () => {} }
  }
  close() {
    order.push('close')
    return Promise.resolve()
  }
}

class FakeNode {
  port: { onmessage: ((e: MessageEvent<Float32Array>) => void) | null } = { onmessage: null }
  disconnect() {}
}

afterEach(() => {
  vi.unstubAllGlobals()
  order.length = 0
})

function stubAudio(getUserMedia: () => Promise<MediaStream>) {
  vi.stubGlobal('AudioContext', FakeContext)
  vi.stubGlobal('AudioWorkletNode', FakeNode)
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
}

// iOS Safari only starts an AudioContext created/resumed inside the user's tap;
// awaiting the microphone prompt first would leave it suspended (no audio).
test('audio_context_starts_inside_the_gesture_before_the_mic_prompt', async () => {
  const track = { stop: vi.fn() }
  stubAudio(async () => {
    order.push('mic')
    return { getTracks: () => [track] } as unknown as MediaStream
  })
  const rec = await startRecording()
  expect(order.slice(0, 3)).toEqual(['context', 'resume', 'mic'])
  const out = await rec.stop()
  expect(out.ms).toBe(0)
  expect(track.stop).toHaveBeenCalled()
})

test('denied_microphone_closes_the_context', async () => {
  stubAudio(async () => {
    throw new DOMException('denied', 'NotAllowedError')
  })
  await expect(startRecording()).rejects.toThrow()
  expect(order).toContain('close')
})
