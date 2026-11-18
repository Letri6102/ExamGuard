/**
 * Phát hiện tiếng nói TRÊN MÁY thí sinh (Silero VAD qua onnxruntime-web, tự phục vụ ở /vad).
 * Âm thanh không bao giờ được gửi đi — chỉ gửi tỉ lệ có tiếng nói, số đoạn nói, năng lượng.
 * Không tải được mô hình (máy yếu/trình duyệt cũ) -> dùng ngưỡng năng lượng.
 */
import { audioWindow } from "./features";

type Frame = { speech: boolean; rms: number };

export class AudioMonitor {
  private frames: Frame[] = [];
  private vad: any = null;
  private ctx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private timer = 0;
  private lastProb = 0;
  mode: "silero" | "energy" | "off" = "off";

  async start(stream: MediaStream, useModel: boolean): Promise<void> {
    if (!stream.getAudioTracks().length) return;
    this.ctx = new AudioContext();
    await this.ctx.resume().catch(() => {});
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.ctx.createMediaStreamSource(stream).connect(this.analyser);
    if (useModel) {
      try {
        const { MicVAD } = await import("@ricky0123/vad-web");
        this.vad = await MicVAD.new({
          model: "v5",
          baseAssetPath: "/vad/",
          onnxWASMBasePath: "/vad/",
          getStream: async () => stream,
          pauseStream: async () => {},
          resumeStream: async (s: MediaStream) => s,
          positiveSpeechThreshold: 0.6,
          negativeSpeechThreshold: 0.4,
          onFrameProcessed: (p: { isSpeech: number }) => { this.lastProb = p.isSpeech; },
          onSpeechStart: () => {},
          onSpeechEnd: () => {},
        });
        await this.vad.start();
        this.mode = "silero";
      } catch (e) {
        console.warn("Silero VAD không chạy được, dùng ngưỡng năng lượng", e);
        this.vad = null;
      }
    }
    if (!this.vad) this.mode = "energy";
    const buf = new Float32Array(this.analyser.fftSize);
    let loud = 0;
    this.timer = window.setInterval(() => {
      if (!this.analyser) return;
      this.analyser.getFloatTimeDomainData(buf);
      let s = 0;
      for (const x of buf) s += x * x;
      const rms = Math.sqrt(s / buf.length);
      let speech: boolean;
      if (this.mode === "silero") speech = this.lastProb >= 0.6;
      else {
        loud = rms > 0.03 ? loud + 1 : 0;
        speech = loud >= 3; // năng lượng cao kéo dài ≥ 300 ms
      }
      this.frames.push({ speech, rms });
    }, 100);
  }

  window(): { values: Record<string, number>; available: boolean } {
    const f = this.frames;
    this.frames = [];
    return { values: audioWindow(f), available: this.mode !== "off" && f.length > 0 };
  }

  async stop(): Promise<void> {
    clearInterval(this.timer);
    try {
      await this.vad?.destroy();
    } catch {
      /* bỏ qua */
    }
    await this.ctx?.close().catch(() => {});
    this.mode = "off";
  }
}
