/**
 * Hàm thuần tính đặc trưng — dùng chung cho trình duyệt, kiểm thử và mô tả trong notebook
 * (notebooks/01 và 03 cài lại đúng các công thức này bằng Python).
 */

// ---------------------------------------------------------------- hướng đầu từ 6 điểm BlazeFace
// Thứ tự điểm của MediaPipe FaceDetector: 0 mắt phải, 1 mắt trái, 2 mũi, 3 miệng, 4 tai phải, 5 tai trái
// (tọa độ chuẩn hóa 0..1 theo khung hình; "phải/trái" là của người trong ảnh).
export type Pt = { x: number; y: number };

export function headPose(kps: Pt[]): { yaw: number; pitch: number } {
  if (!kps || kps.length < 4) return { yaw: 0, pitch: 0 };
  const [re, le, nose, mouth] = kps;
  const eyeMid = { x: (re.x + le.x) / 2, y: (re.y + le.y) / 2 };
  const eyeDist = Math.hypot(le.x - re.x, le.y - re.y) || 1e-6;
  const roll = Math.atan2(le.y - re.y, le.x - re.x);
  const rot = (p: Pt) => {
    const dx = p.x - eyeMid.x;
    const dy = p.y - eyeMid.y;
    return { x: dx * Math.cos(-roll) - dy * Math.sin(-roll), y: dx * Math.sin(-roll) + dy * Math.cos(-roll) };
  };
  const n = rot(nose);
  const m = rot(mouth);
  // mũi nhô ~0.6 lần khoảng cách hai mắt -> yaw_ratio ≈ 0.6·tan(yaw); tỉ lệ dọc mũi/miệng ~0.55 khi nhìn thẳng
  const yaw = (Math.atan(n.x / eyeDist / 0.6) * 180) / Math.PI;
  const vertical = Math.max(m.y, 1e-6);
  const pitch = (n.y / vertical - 0.55) * 120;
  return { yaw: clamp(yaw, -90, 90), pitch: clamp(pitch, -60, 60) };
}

// ---------------------------------------------------------------- thống kê
export const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x));
export const mean = (a: number[]) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
export const std = (a: number[]) => {
  if (a.length < 2) return 0;
  const m = mean(a);
  return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1));
};
export const median = (a: number[]) => {
  if (!a.length) return 0;
  const s = [...a].sort((x, y) => x - y);
  const k = s.length >> 1;
  return s.length % 2 ? s[k] : (s[k - 1] + s[k]) / 2;
};

// ---------------------------------------------------------------- khuôn mặt theo cửa sổ
export type FaceSample = { t: number; count: number; yaw: number; pitch: number; cx: number; cy: number; size: number; conf: number };

export function faceWindow(samples: FaceSample[]): Record<string, number> {
  const n = samples.length || 1;
  const present = samples.filter((s) => s.count > 0);
  const yaws = present.map((s) => s.yaw);
  const pitches = present.map((s) => s.pitch);
  let shift = 0;
  for (let i = 1; i < present.length; i++) shift += Math.hypot(present[i].cx - present[i - 1].cx, present[i].cy - present[i - 1].cy);
  return {
    face_count_mean: mean(samples.map((s) => s.count)),
    face_count_max: samples.reduce((m, s) => Math.max(m, s.count), 0),
    face_missing_ratio: samples.length ? (samples.length - present.length) / n : 0,
    yaw_mean: mean(yaws),
    yaw_std: std(yaws),
    pitch_mean: mean(pitches),
    pitch_std: std(pitches),
    head_away_ratio: present.length ? present.filter((s) => Math.abs(s.yaw) > 30 || Math.abs(s.pitch) > 25).length / present.length : 0,
    box_shift: shift,
    face_size: mean(present.map((s) => s.size)),
    face_conf: mean(present.map((s) => s.conf)),
  };
}

// ---------------------------------------------------------------- nhịp gõ phím
export type KeyEvent = { t: number; type: "down" | "up"; code: string; ime: boolean };

/** Thời gian giữ phím (dwell) và khoảng cách giữa hai lần nhấn (flight), bỏ qua phím do bộ gõ IME sinh ra. */
export function keyTimings(events: KeyEvent[]): { dwell: number[]; flight: number[]; keys: number; edits: number } {
  const down = new Map<string, number>();
  const dwell: number[] = [];
  const flight: number[] = [];
  let lastDown = -1;
  let keys = 0;
  let edits = 0;
  for (const e of events) {
    if (e.ime) continue; // keyCode 229 / "Process": phím ảo của Unikey, EVKey, bộ gõ hệ điều hành
    if (e.type === "down") {
      if (down.has(e.code)) continue; // tự lặp khi giữ phím
      down.set(e.code, e.t);
      keys++;
      if (e.code === "Backspace" || e.code === "Delete") edits++;
      if (lastDown >= 0) flight.push(e.t - lastDown);
      lastDown = e.t;
    } else {
      const t0 = down.get(e.code);
      if (t0 !== undefined) {
        dwell.push(e.t - t0);
        down.delete(e.code);
      }
    }
  }
  return { dwell: dwell.filter((d) => d > 0 && d < 2000), flight: flight.filter((f) => f > 0 && f < 30000), keys, edits };
}

export type KeyBaseline = { dwell: number; flight: number };

export function keystrokeWindow(
  events: KeyEvent[], compositions: number, insertedChars: number, seconds: number, base: KeyBaseline | null,
): Record<string, number> {
  const { dwell, flight, keys, edits } = keyTimings(events);
  const dm = mean(dwell);
  const fm = mean(flight.filter((f) => f < 5000));
  return {
    keys,
    dwell_rel: base && dm ? dm / base.dwell : dm ? 1 : 0,
    dwell_cv: dm ? std(dwell) / dm : 0,
    flight_rel: base && fm ? fm / base.flight : fm ? 1 : 0,
    flight_cv: fm ? std(flight.filter((f) => f < 5000)) / fm : 0,
    burst_ratio: flight.length ? flight.filter((f) => f < 150).length / flight.length : 0,
    pause_ratio: flight.length ? flight.filter((f) => f > 2000).length / flight.length : 0,
    backspace_ratio: keys ? edits / keys : 0,
    compositions,
    chars_per_sec: insertedChars / Math.max(seconds, 1e-3),
  };
}

// ---------------------------------------------------------------- lớp bảo vệ riêng tư (trên máy thí sinh)
/** Lượng tử hóa + nhiễu Laplace cho đặc trưng nhịp gõ trước khi gửi (đánh giá privacy–utility ở notebook 04). */
export function privatize(v: Record<string, number>, keys: string[], epsilon: number, step: number, rand = Math.random): Record<string, number> {
  const out = { ...v };
  const b = step / Math.max(epsilon, 1e-6);
  for (const k of keys) {
    if (!(k in out)) continue;
    const u = rand() - 0.5;
    const noise = -b * Math.sign(u) * Math.log(1 - 2 * Math.abs(u));
    out[k] = Math.max(0, Math.round((out[k] + noise) / step) * step);
  }
  return out;
}

export const PRIVATE_KEYSTROKE_FEATURES = ["dwell_rel", "dwell_cv", "flight_rel", "flight_cv", "burst_ratio", "pause_ratio"];

// ---------------------------------------------------------------- âm thanh
export function audioWindow(frames: { speech: boolean; rms: number }[]): Record<string, number> {
  let onsets = 0;
  for (let i = 0; i < frames.length; i++) if (frames[i].speech && (i === 0 || !frames[i - 1].speech)) onsets++;
  const rms = mean(frames.map((f) => f.rms));
  return {
    speech_ratio: frames.length ? frames.filter((f) => f.speech).length / frames.length : 0,
    speech_segments: onsets,
    audio_energy: rms > 0 ? clamp((20 * Math.log10(rms) + 60) / 60, 0, 1) : 0,
  };
}

/** Thời gian trả lời so với kỳ vọng theo độ khó: âm nhiều = nhanh bất thường. */
export function timeVsExpected(seconds: number, expected: number): number {
  if (!expected) return 0;
  return clamp((seconds - expected) / (0.5 * expected), -5, 5);
}
