// Plays queued 24 kHz mono PCM (Float32) from the tutor; reports when idle vs speaking.
class Playback extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.offset = 0;
    this.speaking = false;
    this.port.onmessage = (e) => {
      if (e.data === 'clear') { this.queue = []; this.offset = 0; return; }
      this.queue.push(e.data);
    };
  }
  process(inputs, outputs) {
    const out = outputs[0][0];
    let produced = false;
    for (let i = 0; i < out.length; i++) {
      if (this.queue.length === 0) { out[i] = 0; continue; }
      const cur = this.queue[0];
      out[i] = cur[this.offset++];
      produced = true;
      if (this.offset >= cur.length) { this.queue.shift(); this.offset = 0; }
    }
    if (produced !== this.speaking) { this.speaking = produced; this.port.postMessage(produced ? 'speaking' : 'idle'); }
    return true;
  }
}
registerProcessor('playback', Playback);
