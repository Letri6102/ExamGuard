// Tự phục vụ (self-host) file WASM/mô hình để phòng thi không phụ thuộc CDN.
// Chạy tự động sau `npm install`. Mô hình BlazeFace tải một lần từ Google (nếu chưa có).
import { cpSync, existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const nm = "node_modules";
const pub = "public";
const copy = (from, to, filter = () => true) => {
  if (!existsSync(from)) return console.warn("Bỏ qua (không có):", from);
  mkdirSync(to, { recursive: true });
  for (const f of readdirSync(from)) if (filter(f)) cpSync(join(from, f), join(to, f));
};
copy(join(nm, "@mediapipe/tasks-vision/wasm"), join(pub, "mediapipe/wasm"));
copy(join(nm, "@ricky0123/vad-web/dist"), join(pub, "vad"), (f) => f.endsWith(".onnx") || f.endsWith(".worklet.bundle.min.js"));
copy(join(nm, "onnxruntime-web/dist"), join(pub, "vad"), (f) => /^ort-wasm.*\.(wasm|mjs)$/.test(f));

const model = join(pub, "models/blaze_face_short_range.tflite");
if (!existsSync(model)) {
  mkdirSync(join(pub, "models"), { recursive: true });
  const url = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite";
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error("HTTP " + res.status);
    writeFileSync(model, Buffer.from(await res.arrayBuffer()));
    console.log("Đã tải", model);
  } catch (e) {
    console.warn("Không tải được mô hình BlazeFace:", e.message, "\n  Tải tay:", url, "->", model);
  }
}
