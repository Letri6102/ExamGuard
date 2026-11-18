/**
 * Bảo vệ trình duyệt + nhịp gõ phím.
 *  - Không khóa bài: mọi hành vi chỉ được GHI NHẬN thành đặc trưng/sự kiện, giám thị quyết định.
 *  - Bộ gõ tiếng Việt: KHÔNG chặn ở keydown; phím ảo của IME (isComposing / keyCode 229) bị loại khỏi nhịp gõ.
 *  - Chặn dán / kéo thả / menu chuột phải / sao chép đề.
 */
import {
  keyTimings, keystrokeWindow, median, PRIVATE_KEYSTROKE_FEATURES, privatize, type KeyBaseline, type KeyEvent,
} from "./features";

export type GuardEvent = { t: number; k: string; d?: Record<string, unknown> };

type Opts = {
  questionArea: () => HTMLElement | null;
  t0: number;                         // performance.now() lúc bắt đầu làm bài
  onReturn: () => void;               // quay lại tab/cửa sổ -> chụp chùm khung
  onEvent?: (e: GuardEvent) => void;
  warmupSeconds: number;
  epsilon: number;
  quantize: number;
};

export class BrowserGuard {
  private o: Opts;
  private listeners: [EventTarget, string, EventListener, boolean][] = [];
  private c = this.zero();
  private events: GuardEvent[] = [];
  private keys: KeyEvent[] = [];
  private compositions = 0;
  private chars = 0;
  private hiddenSince = 0;
  private blurSince = 0;
  private notFsSince = 0;
  private fullscreenWanted = false;
  private returnAt = -1e9;
  private warmDwell: number[] = [];
  private warmFlight: number[] = [];
  private baseline: KeyBaseline | null = null;
  private lastWindowAt = 0;
  extendedScreen: boolean | null = null;

  constructor(o: Opts) {
    this.o = o;
  }

  private zero() {
    return { hidden: 0, blur: 0, switches: 0, fsExits: 0, notFs: 0, screenChanges: 0, paste: 0, copy: 0, drop: 0,
      ctxmenu: 0, charsAfterReturn: 0 };
  }

  private now = () => (performance.now() - this.o.t0) / 1000;

  private ev(k: string, d?: Record<string, unknown>) {
    const e = { t: Math.round(this.now() * 10) / 10, k, d };
    this.events.push(e);
    this.o.onEvent?.(e);
  }

  private on(target: EventTarget, type: string, fn: (e: any) => void, capture = true) {
    target.addEventListener(type, fn, capture);
    this.listeners.push([target, type, fn, capture]);
  }

  attach(): void {
    const inQuestion = (e: Event) => {
      const q = this.o.questionArea();
      const t = e.target as HTMLElement | null;
      return !!(q && t && q.contains(t) && !(t.closest("textarea, input")));
    };
    this.on(document, "visibilitychange", () => {
      const t = performance.now();
      if (document.hidden) {
        this.hiddenSince = t;
        this.c.switches++;
        this.ev("tab_hidden");
      } else if (this.hiddenSince) {
        const dur = (t - this.hiddenSince) / 1000;
        this.c.hidden += dur;
        this.hiddenSince = 0;
        this.ev("tab_visible", { seconds: Math.round(dur) });
        this.markReturn();
      }
    });
    this.on(window, "blur", () => {
      if (!document.hidden) {
        this.blurSince = performance.now();
        this.ev("window_blur");
      }
    });
    this.on(window, "focus", () => {
      if (this.blurSince) {
        const dur = (performance.now() - this.blurSince) / 1000;
        this.c.blur += dur;
        this.blurSince = 0;
        if (dur >= 1) {
          this.c.switches++;
          this.ev("window_focus", { seconds: Math.round(dur) });
          this.markReturn();
        }
      }
    });
    this.on(document, "fullscreenchange", () => {
      if (!document.fullscreenElement && this.fullscreenWanted) {
        this.c.fsExits++;
        this.notFsSince = performance.now();
        this.ev("fullscreen_exit");
      } else if (document.fullscreenElement && this.notFsSince) {
        this.c.notFs += (performance.now() - this.notFsSince) / 1000;
        this.notFsSince = 0;
        this.ev("fullscreen_enter");
      }
    });
    const scr: any = window.screen;
    if (scr && "addEventListener" in scr) {
      this.on(scr, "change", () => {
        this.c.screenChanges++;
        this.extendedScreen = typeof scr.isExtended === "boolean" ? scr.isExtended : null;
        this.ev("screen_change", { extended: this.extendedScreen });
      });
    }
    // dán / kéo thả: chặn và ghi nhận
    this.on(document, "paste", (e: ClipboardEvent) => { e.preventDefault(); this.c.paste++; this.ev("paste_blocked"); });
    this.on(document, "beforeinput", (e: InputEvent) => {
      if (e.inputType === "insertFromPaste" || e.inputType === "insertFromDrop" || e.inputType === "insertFromPasteAsQuotation") {
        e.preventDefault();
        return;
      }
      if (e.inputType === "insertText" && e.data) this.addChars(e.data.length);
    });
    this.on(document, "drop", (e: DragEvent) => { e.preventDefault(); this.c.drop++; this.ev("drop_blocked"); });
    this.on(document, "dragover", (e: DragEvent) => e.preventDefault());
    this.on(document, "contextmenu", (e: MouseEvent) => { e.preventDefault(); this.c.ctxmenu++; });
    this.on(document, "copy", (e: ClipboardEvent) => {
      if (inQuestion(e) || !(e.target as HTMLElement)?.closest?.("textarea, input")) {
        e.preventDefault();
        this.c.copy++;
        this.ev("copy_blocked");
      }
    });
    this.on(document, "cut", (e: ClipboardEvent) => { if (inQuestion(e)) { e.preventDefault(); this.c.copy++; } });
    this.on(document, "selectstart", (e: Event) => { if (inQuestion(e)) e.preventDefault(); });
    // phím: chỉ ghi thời điểm, không ghi nội dung phím
    this.on(document, "keydown", (e: KeyboardEvent) => {
      const ime = e.isComposing || e.keyCode === 229 || e.key === "Process";
      if ((e.ctrlKey || e.metaKey) && ["p", "s", "u"].includes(e.key.toLowerCase())) e.preventDefault();
      this.keys.push({ t: performance.now(), type: "down", code: e.code || e.key, ime });
    });
    this.on(document, "keyup", (e: KeyboardEvent) => {
      this.keys.push({ t: performance.now(), type: "up", code: e.code || e.key, ime: e.isComposing || e.keyCode === 229 });
    });
    this.on(document, "compositionend", (e: CompositionEvent) => {
      this.compositions++;
      if (e.data) this.addChars(e.data.length);
    });
    this.lastWindowAt = performance.now();
  }

  private addChars(n: number) {
    this.chars += n;
    if (this.now() - this.returnAt <= 10) this.c.charsAfterReturn += n;
  }

  private markReturn() {
    this.returnAt = this.now();
    this.o.onReturn();
  }

  async requestFullscreen(): Promise<boolean> {
    this.fullscreenWanted = true;
    try {
      const req = document.documentElement.requestFullscreen({ navigationUI: "hide" } as FullscreenOptions);
      await Promise.race([req, new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 2000))]);
      this.notFsSince = 0;
      return true;
    } catch {
      this.notFsSince = this.notFsSince || performance.now();
      return false;
    }
  }

  isFullscreen = () => !!document.fullscreenElement;

  window(): { browser: Record<string, number>; keystroke: Record<string, number>; events: GuardEvent[] } {
    const t = performance.now();
    const seconds = Math.max((t - this.lastWindowAt) / 1000, 0.5);
    this.lastWindowAt = t;
    // cộng dồn phần thời gian đang diễn ra
    if (this.hiddenSince) { this.c.hidden += (t - this.hiddenSince) / 1000; this.hiddenSince = t; }
    if (this.blurSince) { this.c.blur += (t - this.blurSince) / 1000; this.blurSince = t; }
    if (this.notFsSince) { this.c.notFs += (t - this.notFsSince) / 1000; this.notFsSince = t; }
    const scr: any = window.screen;
    if (typeof scr?.isExtended === "boolean") this.extendedScreen = scr.isExtended;
    const small = window.innerWidth < 0.85 * screen.availWidth || window.innerHeight < 0.7 * screen.availHeight;
    const browser = {
      hidden_sec: this.c.hidden, blur_sec: this.c.blur, tab_switches: this.c.switches, fullscreen_exits: this.c.fsExits,
      not_fullscreen_sec: this.c.notFs, window_small: small ? 1 : 0, screen_extended: this.extendedScreen ? 1 : 0,
      screen_changes: this.c.screenChanges, paste_attempts: this.c.paste, copy_attempts: this.c.copy,
      drop_attempts: this.c.drop, contextmenu: this.c.ctxmenu, chars_after_return: this.c.charsAfterReturn,
    };
    // nhịp gõ: khởi động (warm-up) để chuẩn hóa theo chính thí sinh, rồi lượng tử hóa + nhiễu Laplace
    const keyEvents = this.keys;
    this.keys = [];
    if (!this.baseline) {
      const { dwell, flight } = keyTimings(keyEvents);
      this.warmDwell.push(...dwell);
      this.warmFlight.push(...flight.filter((f) => f < 5000));
      if (this.now() >= this.o.warmupSeconds && this.warmDwell.length >= 30 && this.warmFlight.length >= 30) {
        this.baseline = { dwell: median(this.warmDwell), flight: median(this.warmFlight) };
      }
    }
    const ks = keystrokeWindow(keyEvents, this.compositions, this.chars, seconds, this.baseline);
    const keystroke = privatize(ks, PRIVATE_KEYSTROKE_FEATURES, this.o.epsilon, this.o.quantize);
    this.c = this.zero();
    this.compositions = 0;
    this.chars = 0;
    const events = this.events;
    this.events = [];
    return { browser, keystroke, events };
  }

  detach(): void {
    for (const [t, type, fn, cap] of this.listeners) t.removeEventListener(type, fn, cap);
    this.listeners = [];
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  }
}
