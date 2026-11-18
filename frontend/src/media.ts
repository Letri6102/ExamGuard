/** Giữ camera/micro và bộ dò mặt giữa các trang của thí sinh (không xin quyền lại khi chuyển trang). */
import { FaceTracker } from "./monitor/face";

let stream: MediaStream | null = null;
let tracker: FaceTracker | null = null;
let trackerReady: Promise<FaceTracker> | null = null;

export async function getStream(): Promise<MediaStream> {
  if (stream && stream.getTracks().every((t) => t.readyState === "live")) return stream;
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("Trình duyệt không hỗ trợ camera (cần HTTPS hoặc localhost).");
  stream = await navigator.mediaDevices.getUserMedia({
    video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
    audio: { echoCancellation: true, noiseSuppression: false },
  });
  return stream;
}

export function getTracker(): Promise<FaceTracker> {
  if (!trackerReady) {
    tracker = new FaceTracker();
    trackerReady = tracker.init().then(() => tracker!).catch((e) => { trackerReady = null; throw e; });
  }
  return trackerReady;
}

export function releaseMedia(): void {
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  tracker?.close();
  tracker = null;
  trackerReady = null;
}
