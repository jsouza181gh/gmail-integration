// Audio worklet for the chat recorder: posts each mono input block (Float32Array)
// to the page, which encodes the WAV (src/lib/wav.ts). Served from the panel's
// own origin so the CSP (script-src 'self') allows it.
class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (channel) this.port.postMessage(channel.slice(0))
    return true
  }
}

registerProcessor('recorder', RecorderProcessor)
