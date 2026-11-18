import { useEffect } from "react";
import type { CandidateWarning } from "../monitor/monitor";
import { warningMessage } from "../monitor/warnings";

export type ShownWarning = CandidateWarning & { id: number; at: number };

/**
 * Cảnh báo cho thí sinh trên trang làm bài.
 *  - toast: góc trên bên phải, tự ẩn sau 8 giây.
 *  - modal: giữa màn hình, phải bấm "Tôi đã hiểu" (tái phạm, bị gắn cờ, khuôn mặt không khớp, tin của giám thị).
 * Không khóa bài: đồng hồ vẫn chạy, bấm xác nhận là làm tiếp.
 */
export default function WarningCenter({ toasts, modal, onDismissToast, onAck }: {
  toasts: ShownWarning[];
  modal: ShownWarning | null;
  onDismissToast: (id: number) => void;
  onAck: (w: ShownWarning) => void;
}) {
  useEffect(() => {
    if (!toasts.length) return;
    const timers = toasts.map((t) => window.setTimeout(() => onDismissToast(t.id), Math.max(0, 8000 - (Date.now() - t.at))));
    return () => timers.forEach(clearTimeout);
  }, [toasts, onDismissToast]);

  return (
    <>
      <div className="toast-stack" aria-live="assertive">
        {toasts.map((t) => {
          const m = warningMessage(t.code, t.count, t.text);
          return (
            <div key={t.id} className={`toast ${m.severity}`} role="alert">
              <div className="toast-head">
                <b>{m.title}</b>
                <button className="toast-x" aria-label="Đóng cảnh báo" onClick={() => onDismissToast(t.id)}>×</button>
              </div>
              <div>{m.text}</div>
            </div>
          );
        })}
      </div>
      {modal && (() => {
        const m = warningMessage(modal.code, modal.count, modal.text);
        return (
          <div className="overlay warning-overlay" role="alertdialog" aria-modal="true" aria-labelledby="warn-title">
            <div className={`card warning-card ${m.severity}`}>
              <h2 id="warn-title">⚠ {m.title}</h2>
              <p>{m.text}</p>
              {modal.code !== "PROCTOR" && modal.count >= 2 && (
                <p className="muted">Số lần đã cảnh báo về hành vi này: {modal.count}</p>
              )}
              <button autoFocus onClick={() => onAck(modal)}>Tôi đã hiểu</button>
            </div>
          </div>
        );
      })()}
    </>
  );
}
