import { useCallback, useEffect, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { auth } from "../api";

export type Staff = { id: number; username: string; role: "admin" | "proctor"; full_name: string };

export function useStaff(): Staff | null {
  const nav = useNavigate();
  const info = auth.info<Staff>("staff");
  useEffect(() => {
    if (!auth.token("staff") || !info) nav("/staff/login", { replace: true });
  }, [info, nav]);
  return info;
}

export function StaffHeader() {
  const staff = auth.info<Staff>("staff");
  const nav = useNavigate();
  const link = (to: string, label: string) => (
    <NavLink to={to} end className={({ isActive }) => (isActive ? "active" : "")}>{label}</NavLink>
  );
  return (
    <header className="top">
      <h1>ExamGuard</h1>
      <nav>
        {link("/proctor", "Theo dõi")}
        {link("/proctor/review", "Rà soát")}
        {link("/proctor/metrics", "Thống kê")}
        {link("/proctor/collusion", "Thông đồng")}
        {staff?.role === "admin" && link("/proctor/leak", "Truy vết lộ đề")}
        {staff?.role === "admin" && link("/admin", "Quản trị")}
        <a href="#" onClick={(e) => { e.preventDefault(); auth.clear("staff"); nav("/staff/login"); }}>
          Đăng xuất ({staff?.username})
        </a>
      </nav>
    </header>
  );
}

export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fn());
      setError("");
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, loading, reload, setData };
}

export function RiskBar({ value, threshold = 60 }: { value: number; threshold?: number }) {
  const color = value >= threshold ? "var(--bad)" : value >= threshold * 0.6 ? "var(--warn)" : "var(--ok)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div className="risk-bar" style={{ flex: 1 }}><span style={{ width: `${Math.min(100, value)}%`, background: color }} /></div>
      <span style={{ fontVariantNumeric: "tabular-nums", minWidth: 32 }}>{Math.round(value)}</span>
    </div>
  );
}

export function Err({ msg }: { msg: string }) {
  return msg ? <div className="alert bad">{msg}</div> : null;
}

export function Card({ title, children, actions }: { title?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="row" style={{ alignItems: "center", marginBottom: 8 }}>
          {title && <h2 style={{ margin: 0 }}>{title}</h2>}
          {actions && <div className="shrink">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export const STATUS_VI: Record<string, string> = {
  precheck: "Kiểm tra thiết bị", verified: "Đã xác thực", in_progress: "Đang làm bài", submitted: "Đã nộp",
  open: "Đang mở", confirmed: "Xác nhận", dismissed: "Bác cờ", draft: "Nháp", closed: "Đã đóng",
};
