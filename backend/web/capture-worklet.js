const TARGET_RATE = 16000;
const FRAME_SAMPLES = 320;

class PaperbookCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samplesPerOutput = sampleRate / TARGET_RATE;
    this.remaining = this.samplesPerOutput;
    this.sum = 0;
    this.frame = new Float32Array(FRAME_SAMPLES);
    this.length = 0;
    this.port.onmessage = ({ data }) => {
      if (data.type !== 'flush') return;
      this.emitFrame();
      this.port.postMessage({ flushed: data.id });
    };
  }

  emitFrame() {
    if (!this.length) return;
    const samples = this.frame.slice(0, this.length);
    this.length = 0;
    this.port.postMessage({ samples }, [samples.buffer]);
  }

  process(inputs) {
    const input = inputs[0][0];
    if (!input) return true;
    for (const sample of input) {
      // Average over each output sample's input interval, including fractions
      // of a sample at 44.1 kHz. Preserve phase across rendering blocks.
      let available = 1;
      while (available > 1e-8) {
        const weight = Math.min(this.remaining, available);
        this.sum += sample * weight;
        available -= weight;
        this.remaining -= weight;
        if (this.remaining <= 1e-8) {
          this.frame[this.length++] = this.sum / this.samplesPerOutput;
          this.remaining = this.samplesPerOutput;
          this.sum = 0;
          if (this.length === FRAME_SAMPLES) this.emitFrame();
        }
      }
    }
    return true;
  }
}

registerProcessor('paperbook-capture', PaperbookCapture);
