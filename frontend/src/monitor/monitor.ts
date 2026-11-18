/**
 * Điều phối giám sát trên máy thí sinh (chỉ việc nhẹ; phần nặng ở server):
 *   BlazeFace 1 fps · VAD · nhịp gõ · sự kiện trình duyệt  -> vector 54 chiều mỗi 2 giây
 *   khung 320px đã làm mờ mặt mỗi 5–10 giây (ngẫu nhiên) + chùm 3–5 khung khi có sự kiện
 *   ảnh crop mặt mỗi 1–3 phút (ngẫu nhiên) + khi mặt xuất hiện lại -> FaceReg xác thực lại
 *   watchdog: giao diện làm bài chậm -> giảm tần suất (vẫn một luồng duy nhất)
 */
import schema from "../../../shared/feature_schema.json";
import { api } from "../api";
import { AudioMonitor } from "./audio";
import { BrowserGuard, type GuardEvent } from "./browser";
import { blurredFrame, faceCrop, FaceTracker, pose, type Face } from "./face";
import { faceWindow, type FaceSample } from "./features";
import { Transport, type NetStatus } from "./transport";
import { WarningDetector, warningForGuardEvent, WarningPolicy, type WarningCode } from "./warnings";

/** Một cảnh báo cần hiện cho thí sinh (đã qua chính sách thời gian chờ / nâng mức). */
export type CandidateWarning = { code: WarningCode; count: number; mode: "toast" | "modal"; source: "client" | "server"; text?: string };

export type AnswerFeatures = () => Record<string, number>;

export type MonitorStatus = {
  net: NetStatus;
  faces: number;
  audio: string;
  fullscreen: boolean;
  lagMs: number;
  framesSent: number;
  lastIdentity: string;
  windowsSent: number;
  degraded: boolean;
};

type Opts = {
  token: string;
  attemptId: number;
  stream: MediaStream;
  video: HTMLVideoElement;
  tracker: FaceTracker;
  answerFeatures: AnswerFeatures;
  questionArea: () => HTMLElement | null;
  reverify: { min: number; max: number };
  lowTier: boolean;
  onStatus: (s: MonitorStatus) => void;
  onEvent?: (e: GuardEvent) => void;
  onWarning?: (w: CandidateWarning) => void;
  warnings?: { enabled: boolean; cooldown: number; counts: Record<string, number>; save: (c: Record<string, number>) => void };
};

const WINDOW_MS = (schema.window_seconds || 2) * 1000;
const rand = (a: number, b: number) => a + Math.random() * (b - a);

export class ExamMonitor {
  private o: Opts;
  private t0 = performance.now();
  private transport: Transport;
  private audio = new AudioMonitor();
  private guard: BrowserGuard;
  private faceSamples: FaceSample[] = [];
  private lastFaces: Face[] = [];
  private timers: number[] = [];
  private windowIndex = 0;
  private faceInterval = 1000;
  private missingSince = 0;
  private awaySince = 0;
  private burstUntil = 0;
  private lagMax = 0;
  private lastRaf = 0;
  private rafId = 0;
  private busyMs = 0;
  private stopped = false;
  private extraEvents: GuardEvent[] = [];
  private detector = new WarningDetector();
  private policy: WarningPolicy;
  status: MonitorStatus = { net: "offline", faces: 0, audio: "off", fullscreen: false, lagMs: 0, framesSent: 0,
    lastIdentity: "", windowsSent: 0, degraded: false };

  constructor(o: Opts) {
    this.o = o;
    this.transport = new Transport(o.token, o.attemptId, (s) => this.update({ net: s }),
      (code, text) => this.warn(code, "server", text));
    const w = o.warnings || { enabled: true, cooldown: 30, counts: {}, save: () => {} };
    this.policy = new WarningPolicy(w.cooldown, w.counts, w.save);
    this.guard = new BrowserGuard({
      questionArea: o.questionArea, t0: this.t0, onReturn: () => this.burst("return"),
      onEvent: (e) => {
        const code = warningForGuardEvent(e.k, e.d);
        if (code) this.warn(code, "client");
        o.onEvent?.(e);
      },
      warmupSeconds: schema.privacy.warmup_seconds, epsilon: schema.privacy.keystroke_laplace_epsilon,
      quantize: schema.privacy.keystroke_quantize,
    });
    if (o.lowTier) this.faceInterval = 1500;
  }

  private elapsed = () => (performance.now() - this.t0) / 1000;

  /** Hiện cảnh báo cho thí sinh (theo chính sách) và ghi lại vào dòng thời gian cho giám thị. */
  warn(code: WarningCode, source: "client" | "server", text?: string): void {
    if (this.stopped || !(this.o.warnings?.enabled ?? true) || !this.o.onWarning) return;
    const d = this.policy.decide(code, this.elapsed());
    if (!d.show) return;
    this.extraEvents.push({ t: Math.round(this.elapsed() * 10) / 10, k: "warning",
      d: { code, n: d.count, src: source, mode: d.mode, ...(text ? { text: text.slice(0, 300) } : {}) } });
    this.o.onWarning({ code, count: d.count, mode: d.mode, source, text });
  }

  /** Thí sinh bấm "Tôi đã hiểu". */
  ackWarning(code: WarningCode): void {
    this.extraEvents.push({ t: Math.round(this.elapsed() * 10) / 10, k: "warning_ack", d: { code } });
  }

  private update(p: Partial<MonitorStatus>) {
    this.status = { ...this.status, ...p };
    this.o.onStatus(this.status);
  }

  async start(): Promise<void> {
    this.guard.attach();
    // không chờ: một số trình duyệt/khung nhúng không bao giờ trả lời yêu cầu toàn màn hình
    this.guard.requestFullscreen().then(() => this.update({ fullscreen: this.guard.isFullscreen() }));
    this.transport.start();
    // VAD tải mô hình ở nền; giám sát hình ảnh/trình duyệt chạy ngay
    this.audio.start(this.o.stream, !this.o.lowTier).catch(() => {}).then(() => this.update({ audio: this.audio.mode }));
    const rafLoop = (t: number) => {
      const gap = t - this.lastRaf;
      // tab bị ẩn thì trình duyệt ngừng vẽ: không tính là máy chậm
      if (this.lastRaf && !document.hidden && gap < 2000) this.lagMax = Math.max(this.lagMax, gap - 16.7);
      this.lastRaf = t;
      this.rafId = requestAnimationFrame(rafLoop);
    };
    this.rafId = requestAnimationFrame(rafLoop);
    this.o.stream.getVideoTracks().forEach((tr) => tr.addEventListener("ended", () => this.warn("CAMERA_OFF", "client")));
    this.loopFace();
    this.timers.push(window.setInterval(() => this.emitWindow(), WINDOW_MS));
    this.scheduleSample();
    this.scheduleReverify(rand(20, 40));
  }

  // ------------------------------------------------------------ khuôn mặt 1 fps
  private loopFace() {
    if (this.stopped) return;
    const t = performance.now();
    const faces = this.o.tracker.detect(this.o.video);
    this.busyMs += performance.now() - t;
    this.lastFaces = faces;
    const now = this.elapsed();
    const main = faces[0];
    const p = main ? pose(main) : { yaw: 0, pitch: 0 };
    this.faceSamples.push({ t: now, count: faces.length, yaw: p.yaw, pitch: p.pitch,
      cx: main ? main.x + main.w / 2 : 0, cy: main ? main.y + main.h / 2 : 0, size: main ? main.w * main.h : 0,
      conf: main?.score ?? 0 });
    // sự kiện -> chùm khung để server kiểm tra ngay
    if (!faces.length) {
      if (!this.missingSince) this.missingSince = now;
      if (now - this.missingSince > 3) this.burst("no_face");
    } else {
      if (this.missingSince && now - this.missingSince >= 5) this.reverify("reappear");
      this.missingSince = 0;
    }
    if (faces.length >= 2) this.burst("multi_face");
    const turned = !!main && (Math.abs(p.yaw) > 30 || Math.abs(p.pitch) > 25);
    for (const code of this.detector.face(now, faces.length, turned)) this.warn(code, "client");
    const away = main && (Math.abs(p.yaw) > 30 || Math.abs(p.pitch) > 25);
    if (away) {
      if (!this.awaySince) this.awaySince = now;
      if (now - this.awaySince > 3) this.burst("head_away");
    } else this.awaySince = 0;
    this.update({ faces: faces.length });
    this.timers.push(window.setTimeout(() => this.loopFace(), this.faceInterval));
  }

  // ------------------------------------------------------------ khung mờ mặt
  private async sendFrame(hi: boolean, why: string) {
    const t = performance.now();
    const blob = await blurredFrame(this.o.video, this.lastFaces);
    this.busyMs += performance.now() - t;
    if (!blob) return;
    const b64 = await blobToB64(blob);
    this.transport.sendFrame(b64, this.elapsed(), hi, why);
    this.update({ framesSent: this.transport.framesSent });
  }

  private scheduleSample() {
    if (this.stopped) return;
    const delay = (this.status.degraded ? rand(10, 15) : rand(5, 10)) * 1000;
    this.timers.push(window.setTimeout(async () => {
      await this.sendFrame(false, "sample").catch(() => {});
      this.scheduleSample();
    }, delay));
  }

  /** 3–5 khung cách nhau 400 ms; tối đa một chùm mỗi 15 giây. */
  burst(why: string) {
    const now = this.elapsed();
    if (now < this.burstUntil || this.stopped) return;
    this.burstUntil = now + 15;
    this.extraEvents.push({ t: Math.round(now * 10) / 10, k: "burst", d: { why } });
    const n = 3 + Math.floor(Math.random() * 3);
    for (let i = 0; i < n; i++) this.timers.push(window.setTimeout(() => this.sendFrame(true, why).catch(() => {}), i * 400));
  }

  // ------------------------------------------------------------ xác thực lại bằng FaceReg
  private scheduleReverify(sec: number) {
    if (this.stopped) return;
    this.timers.push(window.setTimeout(() => this.reverify("periodic"), sec * 1000));
  }

  private reverifyBusy = false;

  async reverify(reason: "periodic" | "reappear") {
    if (this.stopped) return;
    if (this.reverifyBusy) {
      if (reason === "periodic") this.scheduleReverify(15); // không để mất lịch định kỳ
      return;
    }
    const face = this.lastFaces[0];
    if (!face || this.lastFaces.length !== 1) {
      if (reason === "periodic") this.scheduleReverify(15);
      return;
    }
    this.reverifyBusy = true;
    try {
      const blob = await faceCrop(this.o.video, face);
      if (!blob) return;
      const fd = new FormData();
      fd.append("image", blob, "face.jpg");
      fd.append("reason", reason);
      const r = await api("candidate", "/api/exam/reverify", { method: "POST", body: fd });
      this.update({ lastIdentity: r.status });
      if (r.status === "MISMATCH") this.warn("ID_MISMATCH", "client");
      if (reason === "periodic") this.scheduleReverify(r.next_in || rand(this.o.reverify.min, this.o.reverify.max));
    } catch {
      if (reason === "periodic") this.scheduleReverify(60);
    } finally {
      this.reverifyBusy = false;
    }
  }

  // ------------------------------------------------------------ vector 2 giây
  private emitWindow() {
    if (this.stopped) return;
    const t = performance.now();
    const face = faceWindow(this.faceSamples);
    const faceAvail = this.faceSamples.length > 0;
    this.faceSamples = [];
    const audio = this.audio.window();
    if (audio.available) for (const code of this.detector.speech(audio.values.speech_ratio)) this.warn(code, "client");
    const g = this.guard.window();
    const answer = this.o.answerFeatures();
    const lag = Math.max(0, this.lagMax);
    this.lagMax = 0;
    const load = this.busyMs / WINDOW_MS;
    this.busyMs = 0;
    // watchdog: giao diện làm bài giật -> giảm tần suất
    const degraded = lag > 150 || load > 0.15;
    if (degraded !== this.status.degraded) this.faceInterval = degraded ? 2000 : this.o.lowTier ? 1500 : 1000;
    this.busyMs += performance.now() - t;
    const offline = this.transport.offlineInWindow || this.transport.status !== "online";
    this.transport.offlineInWindow = false;
    const w = {
      t: this.windowIndex++,
      v: { ...face, ...audio.values, ...g.browser, ...g.keystroke, ...answer, lag_ms: lag, offline: offline ? 1 : 0,
        monitor_load: load },
      m: { face: faceAvail ? 1 : 0, audio: audio.available ? 1 : 0, browser: 1, keystroke: 1, answer: 1, system: 1 },
      ev: [...g.events, ...this.extraEvents.splice(0)],
    };
    this.transport.pushWindow(w);
    this.update({ lagMs: Math.round(lag), fullscreen: this.guard.isFullscreen(), degraded, windowsSent: this.windowIndex });
  }

  requestFullscreen = () => this.guard.requestFullscreen();
  saveAnswer = (pos: number, value: unknown, seq: number) => this.transport.saveAnswer(pos, value, seq, this.elapsed());
  pendingAnswers = () => this.transport.pendingAnswers();

  async stop(): Promise<void> {
    if (this.stopped) return;
    this.emitWindow();
    this.stopped = true;
    for (const id of this.timers) clearTimeout(id);
    cancelAnimationFrame(this.rafId);
    this.guard.detach();
    await this.audio.stop();
    await this.transport.flushAnswers();
    setTimeout(() => this.transport.close(), 1500); // cho cửa sổ cuối kịp gửi
  }
}

function blobToB64(b: Blob): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(",")[1] || "");
    r.onerror = () => rej(r.error);
    r.readAsDataURL(b);
  });
}
