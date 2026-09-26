// Captures mic audio, downsamples to 16 kHz mono, emits Int16 PCM chunks (~120 ms).
class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = Math.max(1, Math.round(sampleRate / 16000));
    this.buf = [];
    this.flushAt = Math.round(16000 * 0.12);
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
