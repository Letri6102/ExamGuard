import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, auth, fmtTime, wsUrl } from "../../api";
import { ExamSelect, useExamPick } from "../../components/ExamPicker";
import { Card, Err, RiskBar, STATUS_VI, StaffHeader, useStaff } from "../../components/ui";

export default function Dashboard() {
  const staff = useStaff();
  const nav = useNavigate();
  const [examId, pick, exams] = useExamPick();
  const [rows, setRows] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const [live, setLive] = useState(false);

  useEffect(() => {
    if (!examId) return;
    let ws: WebSocket | null = null;
    let closed = false;
    const load = () => api("staff", `/api/proctor/exams/${examId}/attempts`).then(setRows).catch((e) => setErr(e.message));
    load();
    const poll = setInterval(load, 30_000); // đồng bộ lại định kỳ (trạng thái, cờ mới)
    const connect = () => {
      ws = new WebSocket(wsUrl(`/ws/proctor?token=${encodeURIComponent(auth.token("staff"))}&exam_id=${examId}`));
      ws.onopen = () => setLive(true);
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data);
        if (msg.type !== "attempt") return;
        setRows((prev) => {
          const i = prev.findIndex((r) => r.id === msg.id);
          if (i < 0) { load(); return prev; }
          const next = [...prev];
          next[i] = { ...next[i], risk: msg.risk, peak: msg.peak, last_seen: msg.last_seen,
            open_flags: msg.flagged && !next[i].open_flags ? 1 : next[i].open_flags, flagged: next[i].flagged || msg.flagged };
          return next;
        });
      };
      ws.onclose = () => { setLive(false); if (!closed) setTimeout(connect, 3000); };
    };
    connect();
    return () => { closed = true; clearInterval(poll); ws?.close(); };
  }, [examId]);

  const shown = useMemo(() => [...rows].filter((r) => !onlyFlagged || r.open_flags || r.flagged)
    .sort((a, b) => (b.open_flags - a.open_flags) || (b.risk - a.risk)), [rows, onlyFlagged]);
  if (!staff) return null;
  const now = Date.now() / 1000;
  return (
    <>
      <StaffHeader />
      <main>
        <Card title="Theo dõi phiên thi" actions={<span className={live ? "badge ok" : "badge warn"}>{live ? "Trực tiếp" : "Đang kết nối…"}</span>}>
          <div className="row">
            <ExamSelect id={examId} pick={pick} exams={exams} />
            <label className="shrink" style={{ display: "flex", gap: 6, color: "var(--ink)" }}>
              <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} /> Chỉ phiên có cờ
            </label>
          </div>
          <Err msg={err} />
          <p className="muted">Điểm rủi ro do hệ thống tính từ đặc trưng tổng hợp; giám thị xem chi tiết và quyết định. Phiên có dấu ★ nằm trong mẫu rà soát ngẫu nhiên.</p>
          <div className="table-wrap"><table>
            <thead><tr><th>Thí sinh</th><th>Trạng thái</th><th style={{ minWidth: 160 }}>Rủi ro hiện tại</th><th>Cao nhất</th><th>Cờ mở</th><th>Hạng máy</th><th>FaceReg</th><th>Lần cuối</th></tr></thead>
            <tbody>{shown.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => nav(`/proctor/attempt/${r.id}`)}>
                <td><b>{r.full_name}</b> <span className="muted">{r.candidate_code} · {r.pseudonym}</span>{r.review_sample ? " ★" : ""}</td>
                <td>{STATUS_VI[r.status] || r.status}</td>
                <td><RiskBar value={r.risk} /></td>
                <td>{Math.round(r.peak)}</td>
                <td>{r.open_flags ? <span className="badge bad">{r.open_flags}</span> : r.flagged ? <span className="badge">đã xử lý</span> : "–"}</td>
                <td>{r.tier}</td><td>{r.facereg_status || "–"}</td>
                <td className="muted">{r.last_seen ? (now - r.last_seen < 15 ? "vừa xong" : fmtTime(r.last_seen)) : "–"}</td>
              </tr>
            ))}</tbody>
          </table></div>
        </Card>
      </main>
    </>
  );
}
