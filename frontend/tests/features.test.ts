import assert from "node:assert/strict";
import { test } from "node:test";
import {
  audioWindow, faceWindow, headPose, keyTimings, keystrokeWindow, privatize, timeVsExpected, type KeyEvent,
} from "../src/monitor/features.ts";

test("head pose frontal vs turned", () => {
  const front = headPose([{ x: 0.4, y: 0.4 }, { x: 0.6, y: 0.4 }, { x: 0.5, y: 0.51 }, { x: 0.5, y: 0.6 }]);
  assert.ok(Math.abs(front.yaw) < 1 && Math.abs(front.pitch) < 3, JSON.stringify(front));
  const turned = headPose([{ x: 0.4, y: 0.4 }, { x: 0.6, y: 0.4 }, { x: 0.58, y: 0.51 }, { x: 0.55, y: 0.6 }]);
  assert.ok(turned.yaw > 25, JSON.stringify(turned));
  const down = headPose([{ x: 0.4, y: 0.4 }, { x: 0.6, y: 0.4 }, { x: 0.5, y: 0.57 }, { x: 0.5, y: 0.6 }]);
  assert.ok(down.pitch > 20, JSON.stringify(down));
});

test("face window counts missing and multiple faces", () => {
  const w = faceWindow([
    { t: 0, count: 1, yaw: 0, pitch: 0, cx: 0.5, cy: 0.5, size: 0.1, conf: 0.9 },
    { t: 1, count: 0, yaw: 0, pitch: 0, cx: 0, cy: 0, size: 0, conf: 0 },
    { t: 2, count: 2, yaw: 40, pitch: 0, cx: 0.6, cy: 0.5, size: 0.1, conf: 0.8 },
  ]);
  assert.equal(w.face_count_max, 2);
  assert.ok(Math.abs(w.face_missing_ratio - 1 / 3) < 1e-9);
  assert.equal(w.head_away_ratio, 0.5);
  assert.ok(Math.abs(w.box_shift - 0.1) < 1e-9);
});

test("keystroke ignores IME virtual keys and auto-repeat", () => {
  const ev: KeyEvent[] = [
    { t: 0, type: "down", code: "KeyA", ime: false },
    { t: 5, type: "down", code: "KeyA", ime: false }, // tự lặp
    { t: 80, type: "up", code: "KeyA", ime: false },
    { t: 100, type: "down", code: "Unidentified", ime: true }, // Unikey
    { t: 200, type: "down", code: "Backspace", ime: false },
    { t: 260, type: "up", code: "Backspace", ime: false },
  ];
  const k = keyTimings(ev);
  assert.equal(k.keys, 2);
  assert.equal(k.edits, 1);
  assert.deepEqual(k.dwell, [80, 60]);
  assert.deepEqual(k.flight, [200]);
  const w = keystrokeWindow(ev, 1, 3, 2, { dwell: 70, flight: 100 });
  assert.ok(Math.abs(w.dwell_rel - 1) < 1e-9 && w.flight_rel === 2 && w.backspace_ratio === 0.5 && w.chars_per_sec === 1.5);
});

test("privatize adds bounded noise only to private keys", () => {
  let i = 0;
  const seq = [0.9, 0.1, 0.5];
  const out = privatize({ dwell_rel: 1, keys: 7 }, ["dwell_rel"], 2, 0.05, () => seq[i++ % seq.length]);
  assert.equal(out.keys, 7);
  assert.ok(out.dwell_rel !== 1 && out.dwell_rel >= 0);
  assert.ok(Math.abs(out.dwell_rel / 0.05 - Math.round(out.dwell_rel / 0.05)) < 1e-9);
});

test("audio and answer timing", () => {
  const a = audioWindow([{ speech: false, rms: 0.01 }, { speech: true, rms: 0.1 }, { speech: true, rms: 0.1 }, { speech: false, rms: 0.01 }, { speech: true, rms: 0.1 }]);
  assert.equal(a.speech_segments, 2);
  assert.equal(a.speech_ratio, 0.6);
  assert.ok(a.audio_energy > 0 && a.audio_energy < 1);
  assert.equal(timeVsExpected(10, 60), -5 / 3);
});
