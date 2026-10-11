// Captures mic audio, downsamples to the provider's rate (16 kHz Gemini, 24 kHz OpenAI), emits Int16 PCM chunks (~120 ms).
class Capture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const rate = (options && options.processorOptions && options.processorOptions.rate) || 16000;
    this.ratio = Math.max(1, Math.round(sampleRate / rate));
    this.buf = [];
    this.flushAt = Math.round(rate * 0.12);
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i += this.ratio) {
        let sum = 0, n = 0;
        for (let j = 0; j < this.ratio && i + j < ch.length; j++) { sum += ch[i + j]; n++; }
        this.buf.push(sum / n);
      }
      if (this.buf.length >= this.flushAt) {
        const out = new Int16Array(this.buf.length);
        for (let i = 0; i < this.buf.length; i++) {
          const v = Math.max(-1, Math.min(1, this.buf[i]));
          out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
        }
        this.port.postMessage(out.buffer, [out.buffer]);
        this.buf = [];
      }
    }
    return true;
  }
}
registerProcessor('capture', Capture);
