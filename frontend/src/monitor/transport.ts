/**
 * Một luồng duy nhất tới server: WebSocket cho đặc trưng + khung hình, REST cho câu trả lời.
 * Mất mạng: đặc trưng và câu trả lời đệm trong IndexedDB, gửi bù khi có lại (server bỏ trùng).
 * Mất kết nối được ghi nhận là sự kiện "offline", KHÔNG bị coi là gian lận.
 */
import { api, wsUrl } from "../api";
import { Outbox, pad } from "./store";

export type Window = { t: number; v: Record<string, number>; m: Record<string, number>; ev: unknown[] };
type WinItem = { key: string; w: Window };
type AnsItem = { key: string; position: number; value: unknown; client_seq: number; t: number };
export type NetStatus = "online" | "offline" | "rest";

export class Transport {
  private ws: WebSocket | null = null;
  private windows = new Outbox<WinItem>("windows");
  private answers = new Outbox<AnsItem>("answers");
  private retry = 0;
  private failures = 0;
  private closed = false;
  private hiFrames: object[] = [];
  private restTimer = 0;
  private flushing = false;
  status: NetStatus = "offline";
  offlineInWindow = false;
  framesSent = 0;
  framesDropped = 0;

  constructor(private token: string, private attempt: number, private onStatus: (s: NetStatus) => void) {}

  start(): void {
    this.connect();
    this.restTimer = window.setInterval(() => {
      if (this.status !== "online") this.flushRest();
      this.flushAnswers();
    }, 10_000);
    window.addEventListener("online", this.onOnline);
  }

  private onOnline = () => {
    if (!this.ws || this.ws.readyState > 1) this.connect();
    this.flushAnswers();
  };

  private setStatus(s: NetStatus) {
    if (s !== "online") this.offlineInWindow = true;
    if (s !== this.status) {
      this.status = s;
      this.onStatus(s);
    }
  }

  private connect() {
    if (this.closed) return;
    const ws = new WebSocket(wsUrl(`/ws/exam?token=${encodeURIComponent(this.token)}`));
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.failures = 0;
      this.setStatus("online");
      this.flushWs();
      for (const f of this.hiFrames.splice(0)) ws.send(JSON.stringify(f));
    };
    ws.onmessage = (m) => {
      try {
        const msg = JSON.parse(m.data);
        if (msg.type === "ack") this.ackUpTo(msg.t);
      } catch {
        /* bỏ qua */
      }
    };
    ws.onclose = (e) => {
      if (this.closed) return;
      if (e.code === 4409 || e.code === 4401) return; // bài đã nộp / hết phiên
      this.failures++;
      this.setStatus(this.failures >= 3 ? "rest" : "offline");
      const delay = Math.min(30_000, 1000 * 2 ** this.retry++);
      setTimeout(() => this.connect(), delay);
    };
    ws.onerror = () => ws.close();
  }

  private async ackUpTo(t: number) {
    const items = await this.windows.all(`${this.attempt}:`);
    await this.windows.remove(items.filter((x) => x.w.t <= t).map((x) => x.key));
  }

  private async flushWs() {
    if (this.flushing) return;
    this.flushing = true;
    try {
      const items = await this.windows.all(`${this.attempt}:`);
      for (let i = 0; i < items.length; i += 200) {
        if (this.ws?.readyState !== WebSocket.OPEN) break;
        this.ws.send(JSON.stringify({ type: "windows", windows: items.slice(i, i + 200).map((x) => x.w) }));
      }
    } finally {
      this.flushing = false;
    }
  }

  private async flushRest() {
    const items = await this.windows.all(`${this.attempt}:`);
    if (!items.length) return;
    try {
      const r = await api("candidate", "/api/exam/telemetry", { method: "POST", json: { windows: items.slice(0, 500).map((x) => x.w) } });
      await this.ackUpTo(r.ack);
      if (this.status === "offline") this.setStatus("rest");
    } catch {
      this.setStatus("offline");
    }
  }

  async pushWindow(w: Window): Promise<void> {
    await this.windows.put({ key: `${this.attempt}:${pad(w.t)}`, w });
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "windows", windows: [w] }));
  }

  /** Khung mờ mặt. hi = chùm sự kiện (giữ lại khi mất mạng ngắn), lo = mẫu định kỳ (bỏ khi mất mạng). */
  sendFrame(jpegB64: string, t: number, hi: boolean, why: string): void {
    const msg = { type: "frame", jpeg: jpegB64, t, hi, why };
    if (this.ws?.readyState === WebSocket.OPEN && this.ws.bufferedAmount < 2_000_000) {
      this.ws.send(JSON.stringify(msg));
      this.framesSent++;
    } else if (hi && this.hiFrames.length < 15) {
      this.hiFrames.push(msg);
    } else {
      this.framesDropped++;
    }
  }

  async saveAnswer(position: number, value: unknown, clientSeq: number, t: number): Promise<boolean> {
    const item = { key: `${this.attempt}:${pad(position)}`, position, value, client_seq: clientSeq, t };
    await this.answers.put(item);
    return this.flushAnswers();
  }

  async flushAnswers(): Promise<boolean> {
    const items = await this.answers.all(`${this.attempt}:`);
    let ok = true;
    for (const a of items) {
      try {
        await api("candidate", "/api/exam/answer", { method: "POST", json: { position: a.position, value: a.value, client_seq: a.client_seq, t: a.t } });
        const still = (await this.answers.all(a.key)).find((x) => x.key === a.key);
        if (still && still.client_seq === a.client_seq) await this.answers.remove([a.key]);
      } catch (e: any) {
        if (e?.status && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429) await this.answers.remove([a.key]);
        ok = false;
      }
    }
    return ok;
  }

  async pendingAnswers(): Promise<number> {
    return (await this.answers.all(`${this.attempt}:`)).length;
  }

  close(): void {
    this.closed = true;
    clearInterval(this.restTimer);
    window.removeEventListener("online", this.onOnline);
    this.ws?.close();
  }
}
