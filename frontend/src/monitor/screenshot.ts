/**
 * Phát hiện thao tác chụp màn hình từ phím bấm (trang web không đọc được ảnh chụp, chỉ đoán từ phím và focus).
 *
 * macOS: hệ điều hành "nuốt" phím số của Cmd+Shift+3/4/5/6, trang chỉ thấy Cmd và Shift được nhấn rồi thả.
 *  - Thấy được phím số (một số trình duyệt/bàn phím) -> chắc chắn.
 *  - Cmd+Shift nhấn cùng lúc rồi thả mà không có phím nào khác -> gần như chắc chắn là chụp toàn màn hình.
 *    (Phím tắt thường như Cmd+Shift+Z vẫn có keydown của phím Z nên không bị tính.)
 *  - Đang giữ Cmd+Shift mà cửa sổ mất focus -> lớp chọn vùng của Cmd+Shift+4/5 hiện lên.
 * Windows: phím PrintScreen (thường chỉ có keyup); Win+Shift+S hiện lớp chọn vùng -> mất focus khi đang giữ Win+Shift.
 *
 * Chỉ dùng cú pháp TypeScript "erasable" để chạy được trong `node --test`.
 */

export type KeyLike = { key: string; code?: string; metaKey: boolean; shiftKey: boolean; ctrlKey?: boolean };
export type ScreenshotHit = { method: "printscreen" | "shortcut" | "modifier_combo" | "region_overlay"; key?: string };

const SHOT_CODES = ["Digit3", "Digit4", "Digit5", "Digit6", "KeyS"]; // Cmd+Shift+3/4/5/6 (mac), Win+Shift+S
const MODIFIERS = ["Meta", "OS", "Shift", "Control", "Alt", "AltGraph", "CapsLock", "Fn"];
const COMBO_MAX_MS = 4000;

export class ScreenshotDetector {
  private meta = false;
  private shift = false;
  private comboAt = -1;      // thời điểm Cmd/Win + Shift cùng được giữ
  private otherKey = false;  // có phím khác trong lúc giữ tổ hợp (phím tắt bình thường)
  private comboFired = false;
  private lastHit = -1e9;

  private hit(t: number, h: ScreenshotHit): ScreenshotHit[] {
    if (t - this.lastHit < 1500) return []; // keydown + keyup, hoặc phím + mất focus của cùng một lần chụp
    this.lastHit = t;
    return [h];
  }

  private sync(e: KeyLike, t: number) {
    this.meta = e.metaKey;
    this.shift = e.shiftKey;
    if (this.meta && this.shift) {
      if (this.comboAt < 0 || t - this.comboAt > COMBO_MAX_MS) {
        this.comboAt = t;
        this.otherKey = false;
        this.comboFired = false;
      }
    }
  }

  keydown(e: KeyLike, t: number): ScreenshotHit[] {
    if (e.key === "PrintScreen" || e.code === "PrintScreen") return this.hit(t, { method: "printscreen" });
    const isMod = MODIFIERS.includes(e.key);
    // Meta/Shift đang nhấn: cập nhật trạng thái từ chính phím vừa nhấn (metaKey có thể chưa bật ở keydown của Meta)
    this.sync({ ...e, metaKey: e.metaKey || e.key === "Meta" || e.key === "OS", shiftKey: e.shiftKey || e.key === "Shift" }, t);
    if (isMod || this.comboAt < 0 || !(this.meta && this.shift)) return [];
    if (SHOT_CODES.includes(e.code || "") && !e.ctrlKey) {
      this.comboFired = true;
      return this.hit(t, { method: "shortcut", key: e.code });
    }
    if (SHOT_CODES.includes(e.code || "") && e.ctrlKey) { // Cmd+Ctrl+Shift+3/4: chụp vào clipboard
      this.comboFired = true;
      return this.hit(t, { method: "shortcut", key: "Ctrl+" + e.code });
    }
    this.otherKey = true;
    return [];
  }

  keyup(e: KeyLike, t: number): ScreenshotHit[] {
    if (e.key === "PrintScreen" || e.code === "PrintScreen") return this.hit(t, { method: "printscreen" });
    const releasing = e.key === "Meta" || e.key === "OS" || e.key === "Shift";
    let out: ScreenshotHit[] = [];
    if (releasing && this.comboAt >= 0 && !this.otherKey && !this.comboFired && t - this.comboAt <= COMBO_MAX_MS) {
      out = this.hit(t, { method: "modifier_combo" });
      this.comboFired = true;
    }
    this.meta = e.key === "Meta" || e.key === "OS" ? false : e.metaKey;
    this.shift = e.key === "Shift" ? false : e.shiftKey;
    if (!this.meta && !this.shift) this.comboAt = -1;
    return out;
  }

  /** Cửa sổ mất focus. */
  blur(t: number): ScreenshotHit[] {
    const holding = this.meta && this.shift && this.comboAt >= 0 && t - this.comboAt <= COMBO_MAX_MS;
    let out: ScreenshotHit[] = [];
    if (holding && !this.otherKey) {
      out = this.comboFired ? [] : this.hit(t, { method: "region_overlay" });
      this.comboFired = true;
    }
    // sau khi mất focus thường không nhận được keyup: xóa trạng thái để không kẹt
    this.meta = this.shift = false;
    this.comboAt = -1;
    return out;
  }

  /** Lần chụp gần nhất (ms), để bỏ cảnh báo "mất tập trung" trùng với lần chụp vùng. */
  lastHitAt(): number {
    return this.lastHit;
  }
}
