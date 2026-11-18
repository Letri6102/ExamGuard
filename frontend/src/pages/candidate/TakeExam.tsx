import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, auth, fmtSec } from "../../api";
import { Err } from "../../components/ui";
import { getStream, getTracker, releaseMedia } from "../../media";
import { timeVsExpected } from "../../monitor/features";
import WarningCenter, { type ShownWarning } from "../../components/WarningCenter";
import { ExamMonitor, type CandidateWarning, type MonitorStatus } from "../../monitor/monitor";

type Q = { position: number; total: number; type: "single" | "multi" | "essay" | "numeric"; stem: string; options: string[];
  answer: any; expected_seconds: number; per_question_seconds: number | null };

const NET_VI = { online: "Đã kết nối", offline: "Mất kết nối – đang lưu trên máy", rest: "Kết nối dự phòng" };

function watermarkUrl(code: string): string {
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='240' height='140'><text x='10' y='80' transform='rotate(-22 120 70)' font-family='monospace' font-size='22' font-weight='700' fill='%23888'>${code}</text></svg>`;
  return `url("data:image/svg+xml;utf8,${svg}")`;
}

export default function TakeExam() {
  const nav = useNavigate();
  const [st, setSt] = useState<any>(null);
  const [q, setQ] = useState<Q | null>(null);
  const [value, setValue] = useState<any>(null);
  const [answered, setAnswered] = useState<Set<number>>(new Set());
  const [started, setStarted] = useState(false);
  const [mon, setMon] = useState<MonitorStatus | null>(null);
  const [err, setErr] = useState("");
  const [now, setNow] = useState(Date.now());
  const [wmPos, setWmPos] = useState("0px 0px");
  const [confirmSubmit, setConfirmSubmit] = useState(false);
  const [toasts, setToasts] = useState<ShownWarning[]>([]);
  const [modals, setModals] = useState<ShownWarning[]>([]);
  const warnSeq = useRef(0);
  const onWarning = useCallback((w: CandidateWarning) => {
    const shown = { ...w, id: ++warnSeq.current, at: Date.now() };
    if (w.mode === "modal") setModals((q) => (q.some((x) => x.code === w.code) ? q : [...q, shown]));
    else setToasts((t) => [...t.filter((x) => x.code !== w.code), shown].slice(-3));
  }, []);
  const dismissToast = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const ackWarning = useCallback((w: ShownWarning) => {
    monitor.current?.ackWarning(w.code);
    setModals((q) => q.filter((x) => x.id !== w.id));
  }, []);
  const monitor = useRef<ExamMonitor | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const qArea = useRef<HTMLDivElement>(null);
  const seq = useRef<Record<number, number>>({});
  const tracker = useRef({ switches: 0, changes: 0, qStart: performance.now(), qStartWall: Date.now(), lastVsExpected: 0, expected: 60 });
  const offset = useRef(0);
  const submitting = useRef(false);

  // ------------------------------------------------------------ tải trạng thái
  useEffect(() => {
    if (!auth.token("candidate")) { nav("/exam/login"); return; }
    api("candidate", "/api/exam/state").then((s) => {
      if (s.attempt.status === "submitted") return nav("/exam/done", { replace: true });
      if (s.attempt.status !== "in_progress") return nav("/exam/check", { replace: true });
      offset.current = s.attempt.server_time * 1000 - Date.now();
      setSt(s);
      setAnswered(new Set(s.progress.answered_positions as number[]));
    }).catch((e) => setErr(e.message));
    return () => { monitor.current?.stop(); };
  }, [nav]);

  const answerFeatures = useCallback(() => {
    const t = tracker.current;
    const total = st?.exam.total_questions || 1;
    const out = {
      question_switches: t.switches, answer_changes: t.changes,
      time_on_question: (performance.now() - t.qStart) / 1000,
      progress: answered.size / total, time_vs_expected: t.lastVsExpected,
    };
    t.switches = 0;
    t.changes = 0;
    t.lastVsExpected = 0;
    return out;
  }, [st, answered]);
  const answerFeaturesRef = useRef(answerFeatures);
  answerFeaturesRef.current = answerFeatures;

  // ------------------------------------------------------------ câu hỏi
  const loadQuestion = useCallback(async (pos: number) => {
    try {
      const d: Q = await api("candidate", `/api/exam/question/${pos}`);
      setQ(d);
      setValue(d.answer ?? (d.type === "multi" ? [] : null));
      if (d.answer !== null && d.answer !== undefined && !(Array.isArray(d.answer) && !d.answer.length)) {
        setAnswered((s) => new Set(s).add(pos));
      }
      tracker.current.switches++;
      tracker.current.qStart = performance.now();
      tracker.current.qStartWall = Date.now();
      tracker.current.expected = d.expected_seconds;
      try { sessionStorage.setItem("eg_pos", String(pos)); } catch { /* bỏ qua */ }
    } catch (e: any) {
      setErr(e.message);
    }
  }, []);

  const save = useCallback(async (v: any) => {
    if (!q || !monitor.current) return;
    setValue(v);
    const pos = q.position;
    seq.current[pos] = (seq.current[pos] || Date.now()) + 1;
    const t = tracker.current;
    t.changes++;
    t.lastVsExpected = timeVsExpected((performance.now() - t.qStart) / 1000, t.expected);
    const empty = v === null || v === "" || (Array.isArray(v) && !v.length);
    setAnswered((s) => { const n = new Set(s); if (empty) n.delete(pos); else n.add(pos); return n; });
    await monitor.current.saveAnswer(pos, v, seq.current[pos]);
  }, [q]);

  // ------------------------------------------------------------ bắt đầu (cần thao tác của người dùng cho toàn màn hình)
  const [booting, setBooting] = useState(false);
  const begin = async () => {
    setBooting(true);
    try {
      const stream = await getStream();
      const tr = await getTracker();
      if (video.current) { video.current.srcObject = stream; await video.current.play().catch(() => {}); }
      const info = auth.info<any>("candidate");
      const m = new ExamMonitor({
        token: auth.token("candidate"), attemptId: info?.attempt?.id ?? st.attempt.id, stream, video: video.current!,
        tracker: tr, answerFeatures: () => answerFeaturesRef.current(), questionArea: () => qArea.current,
        reverify: st.reverify, lowTier: st.attempt.device_tier === "low", onStatus: setMon,
        onWarning,
        warnings: {
          enabled: st.warnings?.enabled ?? true, cooldown: st.warnings?.cooldown ?? 30,
          counts: readWarnCounts(st.attempt.id),
          save: (c) => { try { sessionStorage.setItem(`eg_warn_${st.attempt.id}`, JSON.stringify(c)); } catch { /* bỏ qua */ } },
        },
      });
      monitor.current = m;
      await m.start();
      setStarted(true);
      let pos = 0;
      try { pos = Number(sessionStorage.getItem("eg_pos") || 0); } catch { /* bỏ qua */ }
      loadQuestion(Math.min(pos, st.exam.total_questions - 1));
    } catch (e: any) {
      setErr("Không bật được camera/micro hoặc bộ giám sát: " + (e.message || e));
    } finally {
      setBooting(false);
    }
  };

  // ------------------------------------------------------------ đồng hồ, watermark
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 500);
    const wm = setInterval(() => setWmPos(`${Math.round(Math.random() * 200)}px ${Math.round(Math.random() * 120)}px`), 4000);
    return () => { clearInterval(id); clearInterval(wm); };
  }, []);
  const remaining = st ? st.attempt.deadline_at - (now + offset.current) / 1000 : 0;
  const qRemaining = q?.per_question_seconds ? q.per_question_seconds - (now - tracker.current.qStartWall) / 1000 : null;

  const submit = useCallback(async () => {
    if (submitting.current) return;
    submitting.current = true;
    try {
      await monitor.current?.stop();
      await api("candidate", "/api/exam/submit", { method: "POST" });
      releaseMedia();
      try { sessionStorage.removeItem("eg_pos"); } catch { /* bỏ qua */ }
      nav("/exam/done", { replace: true });
    } catch (e: any) {
      submitting.current = false;
      setErr("Chưa nộp được bài: " + e.message + ". Kiểm tra mạng rồi thử lại.");
    }
  }, [nav]);

  useEffect(() => {
    if (st && started && remaining <= 0) submit();
  }, [remaining, st, started, submit]);
  useEffect(() => {
    if (q && qRemaining !== null && qRemaining <= 0 && q.position + 1 < q.total) loadQuestion(q.position + 1);
  }, [qRemaining, q, loadQuestion]);

  const total = st?.exam.total_questions || 0;
  const answeredCount = answered.size;

  if (!st) return <main><Err msg={err} /></main>;
  return (
    <div className="exam-shell">
      <div className="exam-bar">
        <b>{st.exam.title}</b>
        <span className="muted">SBD {st.candidate.code}</span>
        <span className="muted">Đã làm {answeredCount}/{total}</span>
        <span className="timer" aria-live="off">{fmtSec(remaining)}</span>
        <button className="danger" onClick={() => setConfirmSubmit(true)} disabled={!started}>Nộp bài</button>
      </div>
      {mon && (
        <div className="status-strip" style={{ padding: "4px 20px" }}>
          <span className={mon.net === "online" ? "" : "badge warn"}>{NET_VI[mon.net]}</span>
          <span>Khuôn mặt: {mon.faces}</span>
          <span>Âm thanh: {mon.audio === "silero" ? "Silero VAD" : mon.audio === "energy" ? "đo năng lượng" : "tắt"}</span>
          {mon.lastIdentity && <span>Xác thực lại: {mon.lastIdentity}</span>}
          {mon.degraded && <span className="badge warn">Máy chậm: đã giảm tần suất giám sát</span>}
        </div>
      )}
      {started && mon && !mon.fullscreen && (
        <div className="alert warn" style={{ margin: "8px 20px" }}>
          Bạn đã thoát chế độ toàn màn hình (đã được ghi nhận). <button onClick={() => monitor.current?.requestFullscreen()}>Quay lại toàn màn hình</button>
        </div>
      )}
      <div className="exam-body">
        <Err msg={err} />
        <video ref={video} muted playsInline style={{ position: "fixed", right: 12, bottom: 12, width: 120, borderRadius: 8, transform: "scaleX(-1)", opacity: 0.85, zIndex: 20 }} />
        {q && (
          <div className="question" ref={qArea}>
            <div className="watermark" style={{ backgroundImage: watermarkUrl(st.attempt.watermark), backgroundPosition: wmPos }} />
            <div className="muted">Câu {q.position + 1}/{q.total}{q.per_question_seconds && qRemaining !== null ? ` · còn ${fmtSec(qRemaining)} cho câu này` : ""}</div>
            <div className="stem">{q.stem}</div>
            {(q.type === "single" || q.type === "multi") && q.options.map((o, i) => {
              const sel = q.type === "single" ? value === i : (value || []).includes(i);
              const toggle = () => q.type === "single" ? save(i) : save(sel ? value.filter((x: number) => x !== i) : [...(value || []), i].sort());
              return (
                <div key={i} className={"opt" + (sel ? " sel" : "")} onClick={toggle} role={q.type === "single" ? "radio" : "checkbox"} aria-checked={sel} tabIndex={0}
                  onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(); } }}>
                  <b>{String.fromCharCode(65 + i)}.</b><span>{o}</span>
                </div>
              );
            })}
            {q.type === "numeric" && (
              <input type="number" inputMode="decimal" value={value ?? ""} onChange={(e) => setValue(e.target.value)}
                onBlur={(e) => save(e.target.value === "" ? null : Number(e.target.value))} placeholder="Nhập số" />
            )}
            {q.type === "essay" && (
              <textarea rows={10} value={value ?? ""} spellCheck={false} onChange={(e) => setValue(e.target.value)}
                onBlur={(e) => save(e.target.value)} placeholder="Nhập câu trả lời (tự động lưu khi rời ô)" />
            )}
            <div className="row" style={{ marginTop: 16 }}>
              <button className="secondary shrink" disabled={q.position === 0 || !st.exam.allow_back} onClick={() => loadQuestion(q.position - 1)}>← Câu trước</button>
              <span />
              <button className="shrink" disabled={q.position + 1 >= q.total} onClick={async () => {
                if (q.type === "essay" || q.type === "numeric") await save(value);
                loadQuestion(q.position + 1);
              }}>Câu sau →</button>
            </div>
            {st.exam.allow_back && (
              <div className="qnav">
                {Array.from({ length: q.total }, (_, i) => (
                  <button key={i} className={(answered.has(i) ? "done " : "") + (i === q.position ? "cur" : "")} onClick={() => loadQuestion(i)}>{i + 1}</button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      {!started && (
        <div className="overlay">
          <div className="card">
            <h2>Sẵn sàng làm bài</h2>
            <p>Khi bấm bắt đầu, trang sẽ chuyển sang toàn màn hình và bật giám sát trên máy. Còn {fmtSec(remaining)}.</p>
            <Err msg={err} />
            <button onClick={begin} disabled={booting}>{booting ? "Đang khởi động camera và bộ giám sát…" : "Bắt đầu"}</button>
          </div>
        </div>
      )}
      {started && <WarningCenter toasts={toasts} modal={modals[0] || null} onDismissToast={dismissToast} onAck={ackWarning} />}
      {confirmSubmit && (
        <div className="overlay">
          <div className="card">
            <h2>Nộp bài?</h2>
            <p>Bạn đã trả lời {answeredCount}/{total} câu. Sau khi nộp không thể sửa.</p>
            <div className="row">
              <button className="secondary" onClick={() => setConfirmSubmit(false)}>Làm tiếp</button>
              <button className="danger" onClick={submit}>Nộp bài</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function readWarnCounts(attemptId: number): Record<string, number> {
  try {
    return JSON.parse(sessionStorage.getItem(`eg_warn_${attemptId}`) || "{}");
  } catch {
    return {};
  }
}
