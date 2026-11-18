export type Role = "staff" | "candidate";

const KEY: Record<Role, string> = { staff: "eg_staff", candidate: "eg_candidate" };

function read(key: string): string {
  try {
    return sessionStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

export const auth = {
  token: (r: Role) => read(KEY[r]),
  set(r: Role, token: string, extra?: unknown) {
    try {
      sessionStorage.setItem(KEY[r], token);
      if (extra !== undefined) sessionStorage.setItem(KEY[r] + "_info", JSON.stringify(extra));
    } catch {
      /* chế độ riêng tư: chỉ giữ trong bộ nhớ */
    }
  },
  info<T>(r: Role): T | null {
    try {
      return JSON.parse(read(KEY[r] + "_info") || "null");
    } catch {
      return null;
    }
  },
  clear(r: Role) {
    try {
      sessionStorage.removeItem(KEY[r]);
      sessionStorage.removeItem(KEY[r] + "_info");
    } catch {
      /* bỏ qua */
    }
  },
};

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

export async function api<T = any>(role: Role | null, path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(opts.headers);
  if (role) headers.set("Authorization", "Bearer " + auth.token(role));
  let body = opts.body;
  if (opts.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(opts.json);
  }
  let res: Response;
  try {
    res = await fetch(path, { ...opts, headers, body });
  } catch {
    throw new ApiError("Mất kết nối tới máy chủ.", 0);
  }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = typeof data === "object" && data ? (data as any).detail : data;
    throw new ApiError(typeof detail === "string" ? detail : `Lỗi ${res.status}`, res.status);
  }
  return data as T;
}

/** Tải file (CSV, ảnh bằng chứng) có kèm token. */
export async function apiBlob(path: string): Promise<Blob> {
  const res = await fetch(path, { headers: { Authorization: "Bearer " + auth.token("staff") } });
  if (!res.ok) throw new ApiError(`Lỗi ${res.status}`, res.status);
  return res.blob();
}

export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

export const fmtTime = (t?: number | null) => (t ? new Date(t * 1000).toLocaleString("vi-VN") : "–");
export const fmtSec = (s: number) => {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s % 60).padStart(2, "0");
};
