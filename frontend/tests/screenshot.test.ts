import assert from "node:assert/strict";
import { test } from "node:test";
import { ScreenshotDetector, type KeyLike } from "../src/monitor/screenshot.ts";

const k = (key: string, code: string, meta: boolean, shift: boolean, ctrl = false): KeyLike =>
  ({ key, code, metaKey: meta, shiftKey: shift, ctrlKey: ctrl });

test("Mac Cmd+Shift+3: hệ điều hành nuốt phím 3, chỉ thấy Cmd, Shift nhấn rồi thả", () => {
  const d = new ScreenshotDetector();
  assert.deepEqual(d.keydown(k("Meta", "MetaLeft", true, false), 0), []);
  assert.deepEqual(d.keydown(k("Shift", "ShiftLeft", true, true), 50), []);
  const out = [...d.keyup(k("Shift", "ShiftLeft", true, false), 300), ...d.keyup(k("Meta", "MetaLeft", false, false), 320)];
  assert.deepEqual(out, [{ method: "modifier_combo" }]);
});

test("Mac Cmd+Shift+3 khi trình duyệt nhận được phím 3: chỉ báo một lần", () => {
  const d = new ScreenshotDetector();
  d.keydown(k("Meta", "MetaLeft", true, false), 0);
  d.keydown(k("Shift", "ShiftLeft", true, true), 20);
  assert.deepEqual(d.keydown(k("#", "Digit3", true, true), 40), [{ method: "shortcut", key: "Digit3" }]);
  assert.deepEqual([...d.keyup(k("Shift", "ShiftLeft", true, false), 200), ...d.keyup(k("Meta", "MetaLeft", false, false), 210)], []);
});

test("Mac Cmd+Shift+4: lớp chọn vùng làm mất focus khi đang giữ tổ hợp", () => {
  const d = new ScreenshotDetector();
  d.keydown(k("Meta", "MetaLeft", true, false), 0);
  d.keydown(k("Shift", "ShiftLeft", true, true), 20);
  assert.deepEqual(d.blur(100), [{ method: "region_overlay" }]);
  assert.deepEqual(d.keyup(k("Meta", "MetaLeft", false, false), 3000), []); // không kẹt trạng thái, không báo lại
  assert.ok(d.lastHitAt() === 100);
});

test("phím tắt bình thường không bị tính: Cmd+Shift+Z, gõ chữ hoa, Cmd+Tab", () => {
  const d = new ScreenshotDetector();
  d.keydown(k("Meta", "MetaLeft", true, false), 0);
  d.keydown(k("Shift", "ShiftLeft", true, true), 20);
  assert.deepEqual(d.keydown(k("z", "KeyZ", true, true), 40), []);
  assert.deepEqual([...d.keyup(k("Shift", "ShiftLeft", true, false), 200), ...d.keyup(k("Meta", "MetaLeft", false, false), 220)], []);
  // chữ hoa: chỉ Shift
  d.keydown(k("Shift", "ShiftLeft", false, true), 1000);
  d.keydown(k("A", "KeyA", false, true), 1010);
  assert.deepEqual(d.keyup(k("Shift", "ShiftLeft", false, false), 1100), []);
  // Cmd+Tab: chỉ Cmd rồi mất focus
  d.keydown(k("Meta", "MetaLeft", true, false), 2000);
  assert.deepEqual(d.blur(2100), []);
});

test("Windows: PrintScreen (chỉ có keyup) và Win+Shift+S", () => {
  const d = new ScreenshotDetector();
  assert.deepEqual(d.keyup(k("PrintScreen", "PrintScreen", false, false), 0), [{ method: "printscreen" }]);
  assert.deepEqual(d.keydown(k("PrintScreen", "PrintScreen", false, false), 100), []); // cùng một lần bấm
  d.keydown(k("Meta", "MetaLeft", true, false), 5000);
  d.keydown(k("Shift", "ShiftLeft", true, true), 5020);
  assert.deepEqual(d.keydown(k("S", "KeyS", true, true), 5040), [{ method: "shortcut", key: "KeyS" }]);
  assert.deepEqual(d.blur(5100), []); // lớp chọn vùng ngay sau đó: không báo trùng
});
