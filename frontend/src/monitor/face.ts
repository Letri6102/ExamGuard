/** Dò mặt BlazeFace (MediaPipe Tasks, WASM tự phục vụ ở /mediapipe/wasm) + làm mờ mặt + cắt ảnh mặt. */
import { FaceDetector, FilesetResolver } from "@mediapipe/tasks-vision";
import { headPose, type Pt } from "./features";

export type Face = { x: number; y: number; w: number; h: number; score: number; kps: Pt[] }; // tọa độ chuẩn hóa 0..1

export class FaceTracker {
  private det: FaceDetector | null = null;
  lastMs = 0;

  async init(): Promise<void> {
    const fileset = await FilesetResolver.forVisionTasks("/mediapipe/wasm");
    const opts = (delegate: "GPU" | "CPU") => ({
      baseOptions: { modelAssetPath: "/models/blaze_face_short_range.tflite", delegate },
      runningMode: "VIDEO" as const,
      minDetectionConfidence: 0.5,
    });
    try {
      this.det = await FaceDetector.createFromOptions(fileset, opts("GPU"));
    } catch {
      this.det = await FaceDetector.createFromOptions(fileset, opts("CPU")); // máy không có WebGL
    }
  }

  detect(video: HTMLVideoElement): Face[] {
    if (!this.det || video.readyState < 2 || !video.videoWidth) return [];
    const t0 = performance.now();
    const res = this.det.detectForVideo(video, t0);
    this.lastMs = performance.now() - t0;
    const W = video.videoWidth;
    const H = video.videoHeight;
    return res.detections.map((d) => {
      const b = d.boundingBox!;
      return {
        x: b.originX / W, y: b.originY / H, w: b.width / W, h: b.height / H,
        score: d.categories?.[0]?.score ?? 0,
        kps: (d.keypoints || []).map((k) => ({ x: k.x, y: k.y })),
      };
    }).sort((a, b) => b.w * b.h - a.w * a.h);
  }

  /** Đo tốc độ trên máy thí sinh (dùng để xếp hạng máy, C5). */
  benchmark(video: HTMLVideoElement, n = 5): number {
    const times: number[] = [];
    for (let i = 0; i < n; i++) {
      this.detect(video);
      times.push(this.lastMs);
    }
    times.sort((a, b) => a - b);
    return times[Math.floor(times.length / 2)] || 0;
  }

  close(): void {
    this.det?.close();
    this.det = null;
  }
}

export const pose = (f: Face) => headPose(f.kps);

/** Khung 320px, mặt được làm mờ (pixel hóa + blur) — chỉ khung này được gửi cho YOLO ở server. */
export function blurredFrame(video: HTMLVideoElement, faces: Face[], width = 320): Promise<Blob | null> {
  const W = width;
  const H = Math.round((video.videoHeight / video.videoWidth) * W) || 240;
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const ctx = c.getContext("2d")!;
  ctx.drawImage(video, 0, 0, W, H);
  const tiny = document.createElement("canvas");
  for (const f of faces) {
    const cx = (f.x + f.w / 2) * W;
    const cy = (f.y + f.h / 2) * H;
    const w = f.w * W * 1.5;
    const h = f.h * H * 1.7;
    const x0 = Math.max(0, cx - w / 2);
    const y0 = Math.max(0, cy - h / 2);
    const ww = Math.min(W - x0, w);
    const hh = Math.min(H - y0, h);
    if (ww <= 0 || hh <= 0) continue;
    tiny.width = 6;
    tiny.height = 8;
    const t = tiny.getContext("2d")!;
    t.drawImage(c, x0, y0, ww, hh, 0, 0, 6, 8);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(tiny, 0, 0, 6, 8, x0, y0, ww, hh);
    ctx.imageSmoothingEnabled = true;
  }
  return new Promise((res) => c.toBlob(res, "image/jpeg", 0.7));
}

/** Ảnh cắt khuôn mặt (không làm mờ) để FaceReg xác thực lại — gửi 1–3 phút/lần, server xử lý xong bỏ. */
export function faceCrop(video: HTMLVideoElement, f: Face, size = 256): Promise<Blob | null> {
  const W = video.videoWidth;
  const H = video.videoHeight;
  const side = Math.max(f.w * W, f.h * H) * 1.8;
  const cx = (f.x + f.w / 2) * W;
  const cy = (f.y + f.h / 2) * H;
  const c = document.createElement("canvas");
  c.width = size;
  c.height = size;
  const ctx = c.getContext("2d")!;
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, size, size);
  ctx.drawImage(video, cx - side / 2, cy - side / 2, side, side, 0, 0, size, size);
  return new Promise((res) => c.toBlob(res, "image/jpeg", 0.9));
}
