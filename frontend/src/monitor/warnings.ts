/**
 * Cảnh báo tức thời cho thí sinh: nội dung, phát hiện trên máy, và chính sách nâng mức khi tái phạm.
 *
 *  - Lần đầu: thông báo nhỏ, tự ẩn.
 *  - Tái phạm (cùng loại) hoặc loại nghiêm trọng: hộp thoại, thí sinh phải bấm "Tôi đã hiểu".
 *  - Mỗi loại có thời gian chờ để không làm phiền liên tục.
 * Cảnh báo không khóa bài; mọi lần hiện và xác nhận đều được ghi lại cho giám thị.
 * (Chỉ dùng cú pháp TypeScript "erasable" để chạy được trong `node --test`.)
 */

export type Severity = "info" | "warn" | "bad";

export const WARNINGS = {
  TAB_AWAY: { title: "Bạn vừa rời khỏi trang thi", text: "Rời tab hoặc chuyển sang ứng dụng khác trong giờ thi là không được phép.", severity: "bad" },
  WINDOW_BLUR: { title: "Cửa sổ thi bị mất tập trung", text: "Hãy giữ cửa sổ bài thi ở phía trước và không mở ứng dụng khác.", severity: "warn" },
  FULLSCREEN_EXIT: { title: "Bạn đã thoát chế độ toàn màn hình", text: "Hãy quay lại toàn màn hình để tiếp tục làm bài.", severity: "warn" },
  PASTE: { title: "Không được dán nội dung", text: "Bài thi không cho phép dán hoặc kéo thả nội dung từ nơi khác.", severity: "bad" },
  COPY: { title: "Không được sao chép đề thi", text: "Sao chép nội dung đề thi là không được phép.", severity: "warn" },
  SCREENSHOT: { title: "Phát hiện thao tác chụp màn hình", text: "Chụp hoặc ghi lại màn hình đề thi là không được phép.", severity: "bad" },
  EXTENDED_SCREEN: { title: "Phát hiện màn hình phụ", text: "Hãy tháo màn hình phụ khỏi máy trong giờ thi.", severity: "bad" },
  NO_FACE: { title: "Không thấy khuôn mặt của bạn", text: "Hãy ngồi ngay ngắn trước camera, đủ ánh sáng và không che camera.", severity: "warn" },
  MULTI_FACE: { title: "Có người khác trong khung hình", text: "Trong giờ thi chỉ thí sinh được có mặt trước camera.", severity: "bad" },
  HEAD_AWAY: { title: "Bạn đang nhìn ra ngoài màn hình quá lâu", text: "Hãy tập trung nhìn vào bài thi.", severity: "warn" },
  SPEECH: { title: "Phát hiện tiếng nói", text: "Hãy giữ yên lặng; không trao đổi với người khác trong giờ thi.", severity: "warn" },
  CAMERA_OFF: { title: "Camera đã bị tắt", text: "Hãy bật lại camera. Bài thi cần camera trong suốt thời gian làm bài.", severity: "bad" },
  ID_MISMATCH: { title: "Khuôn mặt không khớp với ảnh đăng ký", text: "Chỉ thí sinh đã đăng ký mới được làm bài. Kết quả đã được chuyển cho giám thị.", severity: "bad" },
  PHONE: { title: "Phát hiện điện thoại", text: "Hãy cất điện thoại ra khỏi tầm tay và khung hình.", severity: "bad" },
  PERSON_EXTRA: { title: "Phát hiện người thứ hai", text: "Trong giờ thi chỉ thí sinh được có mặt trong phòng.", severity: "bad" },
  HEADPHONE: { title: "Phát hiện tai nghe", text: "Không được dùng tai nghe trong giờ thi.", severity: "bad" },
  BOOK: { title: "Phát hiện sách hoặc tài liệu", text: "Hãy cất tài liệu ra khỏi bàn thi.", severity: "warn" },
  SCREEN: { title: "Phát hiện thiết bị màn hình khác", text: "Hãy tắt và cất laptop, máy tính bảng hoặc màn hình khác.", severity: "bad" },
  FLAGGED: { title: "Phiên thi có nhiều dấu hiệu bất thường", text: "Phiên thi của bạn đã được chuyển cho giám thị xem xét. Hãy làm bài nghiêm túc.", severity: "bad" },
  PROCTOR: { title: "Cảnh báo từ giám thị", text: "", severity: "bad" },
} satisfies Record<string, { title: string; text: string; severity: Severity }>;

export type WarningCode = keyof typeof WARNINGS;

const ALWAYS_MODAL: WarningCode[] = ["FLAGGED", "PROCTOR", "ID_MISMATCH"];

export type WarningDecision = { show: boolean; mode: "toast" | "modal"; count: number };

/** Chính sách: thời gian chờ theo loại, đếm số lần đã hiện (giữ được khi tải lại trang nhờ storage). */
export class WarningPolicy {
  private last: Record<string, number> = {};
  counts: Record<string, number>;
  cooldown: number;
  private save: (c: Record<string, number>) => void;

  constructor(cooldownSec: number, initial: Record<string, number> = {}, save: (c: Record<string, number>) => void = () => {}) {
    this.cooldown = cooldownSec;
    this.counts = { ...initial };
    this.save = save;
  }

  decide(code: WarningCode, t: number): WarningDecision {
    const always = code === "PROCTOR"; // tin nhắn của giám thị luôn hiện
    if (!always && this.last[code] !== undefined && t - this.last[code] < this.cooldown) {
      return { show: false, mode: "toast", count: this.counts[code] || 0 };
    }
    this.last[code] = t;
    const count = (this.counts[code] || 0) + 1;
    this.counts[code] = count;
    this.save(this.counts);
    return { show: true, mode: count >= 2 || ALWAYS_MODAL.includes(code) ? "modal" : "toast", count };
  }
}

/** Phát hiện trên máy từ luồng khuôn mặt (1 fps) và âm thanh (mỗi cửa sổ 2 giây). Mỗi "đợt" chỉ báo một lần. */
export class WarningDetector {
  private missingSince = -1;
  private noFaceFired = false;
  private multiStreak = 0;
  private multiFired = false;
  private awaySince = -1;
  private awayFired = false;
  private speechStreak = 0;
  private speechFired = false;
  noFaceSec: number;
  awaySec: number;

  constructor(noFaceSec = 5, awaySec = 5) {
    this.noFaceSec = noFaceSec;
    this.awaySec = awaySec;
  }

  face(t: number, count: number, away: boolean): WarningCode[] {
    const out: WarningCode[] = [];
    if (count === 0) {
      if (this.missingSince < 0) this.missingSince = t;
      if (!this.noFaceFired && t - this.missingSince >= this.noFaceSec) {
        out.push("NO_FACE");
        this.noFaceFired = true;
      }
    } else {
      this.missingSince = -1;
      this.noFaceFired = false;
    }
    if (count >= 2) {
      this.multiStreak++;
      if (!this.multiFired && this.multiStreak >= 2) { // 2 mẫu liên tiếp để tránh báo nhầm do nhiễu
        out.push("MULTI_FACE");
        this.multiFired = true;
      }
    } else {
      this.multiStreak = 0;
      this.multiFired = false;
    }
    if (count >= 1 && away) {
      if (this.awaySince < 0) this.awaySince = t;
      if (!this.awayFired && t - this.awaySince >= this.awaySec) {
        out.push("HEAD_AWAY");
        this.awayFired = true;
      }
    } else {
      this.awaySince = -1;
      this.awayFired = false;
    }
    return out;
  }

  /** speechRatio của một cửa sổ 2 giây; cần 2 cửa sổ liên tiếp (≥ 4 giây nói) mới cảnh báo. */
  speech(speechRatio: number): WarningCode[] {
    if (speechRatio >= 0.5) {
      this.speechStreak++;
      if (!this.speechFired && this.speechStreak >= 2) {
        this.speechFired = true;
        return ["SPEECH"];
      }
    } else if (speechRatio < 0.2) {
      this.speechStreak = 0;
      this.speechFired = false;
    }
    return [];
  }
}

/** Sự kiện trình duyệt -> cảnh báo. */
export function warningForGuardEvent(kind: string, detail?: Record<string, unknown>): WarningCode | null {
  switch (kind) {
    case "tab_visible": return "TAB_AWAY";
    case "window_focus": return detail && detail.screenshot ? null : "WINDOW_BLUR";
    case "fullscreen_exit": return "FULLSCREEN_EXIT";
    case "paste_blocked":
    case "drop_blocked": return "PASTE";
    case "copy_blocked": return "COPY";
    case "screenshot": return "SCREENSHOT";
    case "screen_change": return detail && detail.extended ? "EXTENDED_SCREEN" : null;
    default: return null;
  }
}

export function warningMessage(code: WarningCode, count: number, custom?: string): { title: string; text: string; severity: Severity } {
  const w = WARNINGS[code];
  const base = custom || w.text;
  const repeat = code === "PROCTOR" ? "" : count >= 2
    ? ` Đây là lần thứ ${count}. Hành vi lặp lại đã được ghi lại để giám thị xem xét.`
    : " Hành vi này đã được ghi lại.";
  return { title: w.title, text: base + repeat, severity: w.severity };
}
