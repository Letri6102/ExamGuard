import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, apiBlob, fmtSec, fmtTime } from "../../api";
import RiskTimeline from "../../components/RiskTimeline";
import { Card, Err, RiskBar, STATUS_VI, StaffHeader, useLoad, useStaff } from "../../components/ui";
import { WARNINGS, type WarningCode } from "../../monitor/warnings";

const MOD_VI: Record<string, string> = { face: "Khuôn mặt", identity: "Danh tính", object: "Vật thể", audio: "Âm thanh",
  browser: "Trình duyệt", keystroke: "Nhịp gõ", answer: "Bài làm", system: "Hệ thống" };
const EV_VI: Record<string, string> = { tab_hidden: "Rời tab", tab_visible: "Quay lại tab", window_blur: "Mất focus cửa sổ",
  window_focus: "Quay lại cửa sổ", fullscreen_exit: "Thoát toàn màn hình", fullscreen_enter: "Vào lại toàn màn hình",
  paste_blocked: "Chặn dán", copy_blocked: "Chặn sao chép", drop_blocked: "Chặn kéo thả", screen_change: "Đổi cấu hình màn hình",
  burst: "Chụp chùm khung", q_view: "Xem câu", warning: "Đã cảnh báo thí sinh", warning_ack: "Thí sinh bấm \"Tôi đã hiểu\"",
  proctor_warning: "Giám thị gửi cảnh báo" };

function eventDetail(kind: string, d: any): string {
  if (kind === "warning" || kind === "warning_ack") {
    const w = WARNINGS[d?.code as WarningCode];
    const title = w ? w.title : d?.code;
    return kind === "warning" ? `${title}${d?.text ? `: "${d.text}"` : ""} (lần ${d?.n ?? "?"}, ${d?.mode === "modal" ? "hộp thoại" : "thông báo"})` : title;
  }
  if (kind === "proctor_warning") return `"${d?.text}" · ${d?.by} · ${d?.delivered ? "đã tới màn hình thí sinh" : "thí sinh không kết nối"}`;
  return JSON.stringify(d) === "{}" ? "" : JSON.stringify(d);
}

const PRESETS = ["Vui lòng nhìn vào màn hình bài thi.", "Không trao đổi với người khác trong giờ thi.",
  "Hãy cất điện thoại ra khỏi tầm tay.", "Hãy quay lại chế độ toàn màn hình.", "Hãy ngồi ngay ngắn trước camera."];

function SendWarning({ attemptId, active, onSent }: { attemptId: number; active: boolean; onSent: () => void }) {
  const [msg, setMsg] = useState("");
  const [res, setRes] = useState("");
  const [err, setErr] = useState("");
  const send = async () => {
    try {
      const r = await api("staff", `/api/proctor/attempts/${attemptId}/warn`, { method: "POST", json: { message: msg } });
      setRes(r.delivered ? "Đã hiện trên màn hình thí sinh." : "Thí sinh đang mất kết nối: cảnh báo chưa tới, nhưng đã được ghi lại.");
      setErr("");
      setMsg("");
      onSent();
    } catch (e: any) {
      setErr(e.message);
      setRes("");
    }
  };
  return (
    <Card title="Gửi cảnh báo cho thí sinh">
      {!active ? <p className="muted">Chỉ gửi được khi thí sinh đang làm bài.</p> : (
        <>
          <div className="row">{PRESETS.map((p) => <button key={p} className="secondary shrink" onClick={() => setMsg(p)}>{p}</button>)}</div>
          <div className="row" style={{ marginTop: 8 }}>
            <input value={msg} maxLength={300} onChange={(e) => setMsg(e.target.value)} placeholder="Nội dung cảnh báo (hiện ngay trên màn hình thí sinh)" />
            <button className="danger shrink" onClick={send} disabled={msg.trim().length < 3}>Gửi cảnh báo</button>
          </div>
          {res && <div className="alert ok">{res}</div>}
          <Err msg={err} />
        </>
      )}
    </Card>
  );
}

function Evidence({ flagId, names }: { flagId: number; names: string[] }) {
  const [urls, setUrls] = useState<string[]>([]);
  const [show, setShow] = useState(false);
  useEffect(() => {
    if (!show) return;
    let alive = true;
    Promise.all(names.map((n) => apiBlob(`/api/proctor/flags/${flagId}/evidence/${encodeURIComponent(n)}`).then(URL.createObjectURL).catch(() => "")))
      .then((u) => alive && setUrls(u.filter(Boolean)));
    return () => { alive = false; };
  }, [show, flagId, names]);
  if (!names.length) return <span className="muted">Không giữ khung bằng chứng.</span>;
  if (!show) return <button className="secondary" onClick={() => setShow(true)}>Xem {names.length} khung (mặt đã làm mờ, có ghi nhật ký)</button>;
  return <div className="evidence">{urls.map((u, i) => <a key={i} href={u} target="_blank" rel="noreferrer"><img src={u} alt={`Khung bằng chứng ${i + 1}`} /></a>)}</div>;
}

function WindowInspector({ attemptId, t, names }: { attemptId: number; t: number; names: string[] }) {
  const { data } = useLoad(() => api("staff", `/api/proctor/attempts/${attemptId}/windows?start=${Math.max(0, t - 4)}&end=${t + 4}`), [attemptId, t]);
  const row = data?.rows?.reduce((best: any, r: any) => (!best || Math.abs(r.t - t) < Math.abs(best.t - t) ? r : best), null);
  if (!row) return null;
  const nz = names.map((n, i) => [n, row.v[i]] as [string, number]).filter(([, v]) => v);
  return (
    <div>
      <h3>Đặc trưng tại {fmtSec(row.t)} (điểm cửa sổ {row.s.toFixed(2)})</h3>
      <div className="muted">{nz.map(([n, v]) => `${n}=${Math.round(v * 100) / 100}`).join(" · ") || "Tất cả bằng 0"}</div>
    </div>
  );
}

export default function AttemptDetail() {
  const staff = useStaff();
  const { id } = useParams();
  const { data: d, error, reload } = useLoad(() => api("staff", `/api/proctor/attempts/${id}`), [id]);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [pickT, setPickT] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const [verdict, setVerdict] = useState("clean");
  const [vnote, setVnote] = useState("");
  if (!staff) return null;

  const review = async (fid: number, status: string) => {
    try { await api("staff", `/api/proctor/flags/${fid}/review`, { method: "POST", json: { status, note: notes[fid] || "" } }); reload(); }
    catch (e: any) { setErr(e.message); }
  };
  const sessionReview = async () => {
    try { await api("staff", `/api/proctor/attempts/${id}/session-review`, { method: "POST", json: { verdict, note: vnote } }); setVnote(""); reload(); }
    catch (e: any) { setErr(e.message); }
  };

  return (
    <>
      <StaffHeader />
      <main>
        <Err msg={error || err} />
        {d && (
          <>
            <Card title={`${d.candidate.full_name} · ${d.candidate.code}`} actions={<span className="badge">{STATUS_VI[d.attempt.status] || d.attempt.status}</span>}>
              <div className="kpis">
                <div className="kpi"><span className="muted">Rủi ro hiện tại</span><RiskBar value={d.attempt.risk} /></div>
                <div className="kpi"><span className="muted">Cao nhất</span><b>{Math.round(d.attempt.peak)}</b></div>
                <div className="kpi"><span className="muted">Hạng máy</span><b>{d.attempt.tier}</b></div>
                <div className="kpi"><span className="muted">FaceReg check-in</span><b>{d.attempt.facereg_status || "–"}</b></div>
                <div className="kpi"><span className="muted">Mã giả danh / watermark</span><b style={{ fontSize: 15 }}>{d.attempt.pseudonym} / {d.attempt.watermark}</b></div>
                <div className="kpi"><span className="muted">Điểm</span><b>{d.attempt.score ?? "–"}</b></div>
              </div>
              <RiskTimeline points={d.timeline} flags={d.flags} events={d.events} threshold={60} onPick={setPickT} />
              <p className="muted">Bấm vào đồ thị để xem đặc trưng tại thời điểm đó. Độ phủ tín hiệu: {Object.entries(d.modality_coverage)
                .map(([m, v]) => `${MOD_VI[m] || m} ${Math.round((v as number) * 100)}%`).join(" · ")}</p>
              {pickT !== null && <WindowInspector attemptId={d.attempt.id} t={pickT} names={d.feature_names} />}
            </Card>

            <SendWarning attemptId={d.attempt.id} active={d.attempt.status === "in_progress"} onSent={reload} />

            <Card title={`Cờ (${d.flags.length})`}>
              {!d.flags.length && <p className="muted">Không có cờ.</p>}
              {d.flags.map((f: any) => (
                <div key={f.id} className="card" style={{ marginBottom: 10 }}>
                  <div className="row" style={{ alignItems: "center" }}>
                    <div><b>{fmtSec(f.start_t)} – {fmtSec(f.end_t)}</b> · điểm {Math.round(f.score)}</div>
                    <span className={`badge shrink ${f.status === "confirmed" ? "bad" : f.status === "dismissed" ? "" : "warn"}`}>{STATUS_VI[f.status]}</span>
                  </div>
                  <ul>{f.reasons.map(([label, n]: [string, number]) => <li key={label}>{label} <span className="muted">×{n}</span></li>)}</ul>
                  <Evidence flagId={f.id} names={f.evidence} />
                  <div className="row" style={{ marginTop: 8 }}>
                    <input placeholder="Ghi chú của giám thị" value={notes[f.id] ?? f.note ?? ""} onChange={(e) => setNotes({ ...notes, [f.id]: e.target.value })} />
                    <button className="danger shrink" onClick={() => review(f.id, "confirmed")}>Xác nhận gian lận</button>
                    <button className="secondary shrink" onClick={() => review(f.id, "dismissed")}>Bác cờ</button>
                  </div>
                </div>
              ))}
            </Card>

            <div className="grid2">
              <Card title="Xác thực lại danh tính (FaceReg)">
                {!d.identity.length ? <p className="muted">Chưa có.</p> : (
                  <table><thead><tr><th>Lúc</th><th>Kết quả</th><th>Điểm</th><th>Lý do</th></tr></thead>
                    <tbody>{d.identity.map((i: any, k: number) => (
                      <tr key={k}><td>{fmtSec(i.t)}</td><td><span className={`badge ${i.status === "OK" ? "ok" : "bad"}`}>{i.status}</span></td>
                        <td>{i.score?.toFixed?.(3) ?? "–"}</td><td>{i.reason === "reappear" ? "mặt xuất hiện lại" : "định kỳ"}</td></tr>
                    ))}</tbody></table>
                )}
              </Card>
              <Card title="Sự kiện trình duyệt">
                <div style={{ maxHeight: 300, overflow: "auto" }}>
                  <table><tbody>{d.events.filter((e: any) => e.kind !== "q_view").slice(-300).map((e: any, k: number) => (
                    <tr key={k}><td>{fmtSec(e.t)}</td><td>{EV_VI[e.kind] || e.kind}</td><td className="muted">{eventDetail(e.kind, e.detail)}</td></tr>
                  ))}</tbody></table>
                </div>
              </Card>
            </div>

            <Card title="Rà soát cả phiên">
              <p className="muted">Dùng cho phiên nằm trong mẫu ngẫu nhiên (★) để ước lượng tỉ lệ bỏ sót, hoặc khi giám thị muốn kết luận chung.</p>
              {d.reviews.map((r: any, k: number) => <div key={k} className="muted">{fmtTime(r.at)} · {r.by}: <b>{r.verdict}</b> {r.note}</div>)}
              <div className="row">
                <div className="shrink"><label>Kết luận</label>
                  <select value={verdict} onChange={(e) => setVerdict(e.target.value)}>
                    <option value="clean">Không gian lận</option><option value="cheating">Có gian lận</option><option value="unsure">Chưa chắc</option>
                  </select></div>
                <div><label>Ghi chú</label><input value={vnote} onChange={(e) => setVnote(e.target.value)} /></div>
                <button className="shrink" onClick={sessionReview}>Lưu</button>
              </div>
            </Card>
          </>
        )}
      </main>
    </>
  );
}
