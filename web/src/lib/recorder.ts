// Microphone -> AudioWorklet -> Float32 frames -> 16 kHz WAV (lib/wav.ts).
import { encodeWav } from './wav'

export const MAX_RECORDING_MS = 120_000

export interface Recording {
  /** Stops and returns the WAV and its length. */
  stop(): Promise<{ blob: Blob; ms: number }>
  /** Stops and throws the audio away. */
  cancel(): void
}

export interface RecordOptions {
  maxMs?: number
  onTick?: (ms: number) => void
  /** Called once when maxMs is reached; the caller then calls stop(). */
  onLimit?: () => void
}

export function canRecord(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof AudioWorkletNode !== 'undefined'
  )
}

export async function startRecording(opts: RecordOptions = {}): Promise<Recording> {
  const maxMs = opts.maxMs ?? MAX_RECORDING_MS
  // Created and resumed synchronously inside the user's tap: iOS Safari keeps a
  // context started after an await (the mic prompt) suspended, recording silence.
  const ctx = new AudioContext()
  const resumed = ctx.resume()
  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    })
  } catch (err) {
    void ctx.close().catch(() => {})
    throw err
  }
  const release = () => {
    stream.getTracks().forEach((t) => t.stop())
    void ctx.close().catch(() => {})
  }
  try {
    await resumed
    await ctx.audioWorklet.addModule('/recorder-worklet.js')
  } catch (err) {
    release()
    throw err
  }
  const source = ctx.createMediaStreamSource(stream)
  const node = new AudioWorkletNode(ctx, 'recorder')
  const chunks: Float32Array[] = []
  let samples = 0
  let limited = false
  node.port.onmessage = (e: MessageEvent<Float32Array>) => {
    if (limited) return
    chunks.push(e.data)
    samples += e.data.length
    const ms = (samples / ctx.sampleRate) * 1000
    opts.onTick?.(ms)
    if (ms >= maxMs) {
      limited = true
      opts.onLimit?.()
    }
  }
  source.connect(node)
  let stopped = false
  const halt = () => {
    if (stopped) return
    stopped = true
    node.port.onmessage = null
    source.disconnect()
    node.disconnect()
    release()
  }
  return {
    async stop() {
      const rate = ctx.sampleRate
      halt()
      return { blob: encodeWav(chunks, rate), ms: Math.round((samples / rate) * 1000) }
    },
    cancel: halt,
  }
}
