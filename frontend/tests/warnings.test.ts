import assert from "node:assert/strict";
import { test } from "node:test";
import { WarningDetector, WarningPolicy, warningForGuardEvent, warningMessage } from "../src/monitor/warnings.ts";

test("policy: toast lần đầu, hộp thoại khi tái phạm, có thời gian chờ", () => {
  let saved: Record<string, number> = {};
  const p = new WarningPolicy(30, {}, (c) => { saved = { ...c }; });
  assert.deepEqual(p.decide("TAB_AWAY", 10), { show: true, mode: "toast", count: 1 });
  assert.equal(p.decide("TAB_AWAY", 20).show, false); // trong thời gian chờ
  assert.deepEqual(p.decide("TAB_AWAY", 45), { show: true, mode: "modal", count: 2 });
  assert.equal(p.decide("PASTE", 46).mode, "toast"); // loại khác đếm riêng
  assert.equal(p.decide("FLAGGED", 47).mode, "modal"); // loại nghiêm trọng luôn là hộp thoại
  assert.equal(p.decide("PROCTOR", 47).show, true);
  assert.equal(p.decide("PROCTOR", 48).show, true); // tin của giám thị không bị chặn bởi thời gian chờ
  assert.equal(saved.TAB_AWAY, 2);
  const reloaded = new WarningPolicy(30, saved); // tải lại trang vẫn nhớ số lần
  assert.equal(reloaded.decide("TAB_AWAY", 0).count, 3);
});

test("detector: mất mặt, nhiều người, quay đầu chỉ báo một lần mỗi đợt", () => {
  const d = new WarningDetector(5, 5);
  const seen: string[] = [];
  for (let t = 0; t <= 8; t++) seen.push(...d.face(t, 0, false));
  assert.deepEqual(seen, ["NO_FACE"]);
  assert.deepEqual(d.face(9, 1, false), []);
  assert.deepEqual(d.face(10, 2, false), []); // 1 mẫu 2 mặt: chưa báo
  assert.deepEqual(d.face(11, 2, false), ["MULTI_FACE"]);
  assert.deepEqual(d.face(12, 2, false), []);
  const away: string[] = [];
  for (let t = 13; t <= 19; t++) away.push(...d.face(t, 1, true));
  assert.deepEqual(away, ["HEAD_AWAY"]);
  for (let t = 20; t <= 26; t++) d.face(t, 0, false);
  assert.deepEqual(d.face(27, 0, false), []); // đợt mất mặt mới đã báo ở t=25
});

test("detector: tiếng nói cần 2 cửa sổ liên tiếp", () => {
  const d = new WarningDetector();
  assert.deepEqual(d.speech(0.8), []);
  assert.deepEqual(d.speech(0.7), ["SPEECH"]);
  assert.deepEqual(d.speech(0.9), []);
  assert.deepEqual(d.speech(0.1), []);
  assert.deepEqual(d.speech(0.6), []);
  assert.deepEqual(d.speech(0.6), ["SPEECH"]);
});

test("ánh xạ sự kiện và nội dung", () => {
  assert.equal(warningForGuardEvent("tab_visible"), "TAB_AWAY");
  assert.equal(warningForGuardEvent("screen_change", { extended: false }), null);
  assert.equal(warningForGuardEvent("screen_change", { extended: true }), "EXTENDED_SCREEN");
  assert.equal(warningForGuardEvent("q_view"), null);
  assert.match(warningMessage("PASTE", 3).text, /lần thứ 3/);
  assert.equal(warningMessage("PROCTOR", 1, "Cất điện thoại").text, "Cất điện thoại");
});
