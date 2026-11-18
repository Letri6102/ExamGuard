import { useState } from "react";
import { api, apiBlob, fmtTime } from "../../api";
import { Card, Err, STATUS_VI, StaffHeader, useLoad, useStaff } from "../../components/ui";

const SAMPLE = `{
  "type": "single",
  "stem": "Tính {{a}} + {{b}} × 2",
  "variants": ["Giá trị của {{a}} + 2·{{b}} là?"],
  "params": {"a": {"min": 1, "max": 20}, "b": {"min": 2, "max": 9}},
  "options": ["{{a + b*2}}", "{{(a+b)*2}}", "{{a*b}}", "{{a+b}}"],
  "answer": ["A"],
  "difficulty": 0, "expected_seconds": 40
}`;

function Questions() {
  const { data, error, reload } = useLoad(() => api("staff", "/api/admin/questions"));
  const [json, setJson] = useState(SAMPLE);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const create = async () => {
    try {
      await api("staff", "/api/admin/questions", { method: "POST", json: JSON.parse(json) });
      setMsg("Đã thêm câu hỏi.");
      setErr("");
      reload();
    } catch (e: any) {
      setErr(e.message);
    }
  };
  const importFile = async (f: File | undefined) => {
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await api("staff", "/api/admin/questions/import", { method: "POST", body: fd });
      setMsg(`Đã nhập ${r.created} câu.`);
      reload();
    } catch (e: any) {
      setErr(e.message);
    }
  };
  const del = async (id: number) => {
    if (!confirm(`Xóa câu #${id}?`)) return;
    await api("staff", `/api/admin/questions/${id}`, { method: "DELETE" });
    reload();
  };
  return (
    <>
      <Card title="Thêm câu hỏi">
        <p className="muted">Kiểu: <code>single</code>, <code>multi</code>, <code>numeric</code> (đáp án là biểu thức theo tham số), <code>essay</code>.
          Tham số <code>{"{{a}}"}</code> được sinh ngẫu nhiên cho từng thí sinh; <code>variants</code> là các cách diễn đạt khác.</p>
        <textarea rows={12} value={json} onChange={(e) => setJson(e.target.value)} style={{ fontFamily: "monospace", fontSize: 13 }} />
        <div className="row" style={{ marginTop: 8 }}>
          <button className="shrink" onClick={create}>Thêm</button>
          <div><label>Nhập từ file JSON (danh sách) hoặc CSV (type,stem,A,B,C,D,answer,difficulty,expected_seconds)</label>
            <input type="file" accept=".json,.csv" onChange={(e) => importFile(e.target.files?.[0])} /></div>
        </div>
        {msg && <div className="alert ok">{msg}</div>}
        <Err msg={err || error} />
      </Card>
      <Card title={`Ngân hàng câu hỏi (${data?.length ?? 0})`}>
        <div className="table-wrap"><table>
          <thead><tr><th>#</th><th>Kiểu</th><th>Đề</th><th>Phương án / đáp án</th><th>Độ khó</th><th /></tr></thead>
          <tbody>{(data || []).map((q: any) => (
            <tr key={q.id}>
              <td>{q.id}</td><td>{q.type}</td>
              <td>{q.stem}{q.variants.length > 0 && <div className="muted">+{q.variants.length} cách diễn đạt</div>}</td>
              <td className="muted">{q.options.map((o: any) => `${o.id}. ${o.text}`).join(" · ")}<br />Đáp án: {JSON.stringify(q.answer)}</td>
              <td>{q.difficulty}</td>
              <td><button className="danger" onClick={() => del(q.id)}>Xóa</button></td>
            </tr>
          ))}</tbody>
        </table></div>
      </Card>
    </>
  );
}

function ExamEditor({ exam, questions, onChange }: { exam: any; questions: any[]; onChange: () => void }) {
  const [sel, setSel] = useState<number[]>(exam.question_ids);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const roster = useLoad(() => api("staff", `/api/admin/exams/${exam.id}/candidates`), [exam.id]);
  const run = async (fn: () => Promise<any>, ok: string) => {
    try { await fn(); setMsg(ok); setErr(""); onChange(); roster.reload(); } catch (e: any) { setErr(e.message); setMsg(""); }
  };
  const importCsv = (f?: File) => f && run(() => {
    const fd = new FormData();
    fd.append("file", f);
    return api("staff", `/api/admin/exams/${exam.id}/candidates/import`, { method: "POST", body: fd });
  }, "Đã nhập danh sách thí sinh.");
  const download = async () => {
    const b = await apiBlob(`/api/admin/exams/${exam.id}/candidates.csv`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = `${exam.code}_thi_sinh.csv`;
    a.click();
  };
  return (
    <Card title={`${exam.code} · ${exam.title}`} actions={<span className="badge">{STATUS_VI[exam.status]}</span>}>
      <div className="row">
        {["draft", "open", "closed"].map((s) => (
          <button key={s} className={exam.status === s ? "shrink" : "secondary shrink"}
            onClick={() => run(() => api("staff", `/api/admin/exams/${exam.id}/status`, { method: "POST", json: { status: s } }), "Đã đổi trạng thái.")}>
            {STATUS_VI[s]}</button>
        ))}
      </div>
      <h3>Câu hỏi trong đề ({sel.length})</h3>
      <div style={{ maxHeight: 240, overflow: "auto", border: "1px solid var(--line)", borderRadius: 8, padding: 8 }}>
        {questions.map((q) => (
          <label key={q.id} style={{ display: "flex", gap: 8, color: "var(--ink)" }}>
            <input type="checkbox" checked={sel.includes(q.id)} onChange={(e) => setSel(e.target.checked ? [...sel, q.id] : sel.filter((x) => x !== q.id))} />
            #{q.id} [{q.type}] {q.stem.slice(0, 90)}
          </label>
        ))}
      </div>
      <p><button onClick={() => run(() => api("staff", `/api/admin/exams/${exam.id}/questions`, { method: "PUT", json: { question_ids: sel } }), "Đã lưu đề.")}>Lưu đề</button></p>
      <h3>Thí sinh ({roster.data?.length ?? 0})</h3>
      <div className="row">
        <div><label>Nhập CSV: code,full_name[,email][,shift] — mã thí sinh trùng số báo danh bên FaceReg</label>
          <input type="file" accept=".csv" onChange={(e) => importCsv(e.target.files?.[0])} /></div>
        <button className="secondary shrink" onClick={download}>Tải danh sách + mã dự thi</button>
      </div>
      {msg && <div className="alert ok">{msg}</div>}
      <Err msg={err} />
      <div className="table-wrap" style={{ maxHeight: 280, overflow: "auto" }}><table>
        <thead><tr><th>SBD</th><th>Họ tên</th><th>Mã dự thi</th><th>Trạng thái</th><th>Điểm</th></tr></thead>
        <tbody>{(roster.data || []).map((r: any) => (
          <tr key={r.registration_id}><td>{r.code}</td><td>{r.full_name}</td><td><code>{r.access_code}</code></td>
            <td>{r.attempt ? STATUS_VI[r.attempt.status] || r.attempt.status : "–"}</td><td>{r.attempt?.score ?? "–"}</td></tr>
        ))}</tbody>
      </table></div>
    </Card>
  );
}

function Exams() {
  const exams = useLoad(() => api("staff", "/api/admin/exams"));
  const qs = useLoad(() => api("staff", "/api/admin/questions"));
  const [f, setF] = useState({ code: "", title: "", duration_minutes: 60, per_question_seconds: "", shuffle_questions: true,
    shuffle_options: true, use_variants: true, allow_back: true, require_facereg: true });
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const create = async () => {
    try {
      const r = await api("staff", "/api/admin/exams", { method: "POST", json: { ...f, per_question_seconds: f.per_question_seconds ? Number(f.per_question_seconds) : null } });
      setOpen(r.id);
      exams.reload();
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  const flag = (k: keyof typeof f, label: string) => (
    <label style={{ display: "flex", gap: 6, color: "var(--ink)" }}>
      <input type="checkbox" checked={Boolean(f[k])} onChange={(e) => setF({ ...f, [k]: e.target.checked })} />{label}
    </label>
  );
  return (
    <>
      <Card title="Tạo kỳ thi">
        <div className="row">
          <div><label>Mã kỳ thi (trùng mã bên FaceReg)</label><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></div>
          <div><label>Tên</label><input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
          <div><label>Thời gian (phút)</label><input type="number" value={f.duration_minutes} onChange={(e) => setF({ ...f, duration_minutes: Number(e.target.value) })} /></div>
          <div><label>Giới hạn mỗi câu (giây, tùy chọn)</label><input type="number" value={f.per_question_seconds} onChange={(e) => setF({ ...f, per_question_seconds: e.target.value })} /></div>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          {flag("shuffle_questions", "Hoán vị câu")}{flag("shuffle_options", "Hoán vị đáp án")}{flag("use_variants", "Dùng cách diễn đạt khác")}
          {flag("allow_back", "Cho quay lại câu trước")}{flag("require_facereg", "Bắt buộc xác thực FaceReg")}
        </div>
        <p><button onClick={create}>Tạo</button></p>
        <Err msg={err || exams.error} />
      </Card>
      <Card title="Kỳ thi">
        <div className="table-wrap"><table>
          <thead><tr><th>Mã</th><th>Tên</th><th>Số câu</th><th>Thí sinh</th><th>Trạng thái</th><th /></tr></thead>
          <tbody>{(exams.data || []).map((e: any) => (
            <tr key={e.id}><td>{e.code}</td><td>{e.title}</td><td>{e.question_ids.length}</td><td>{e.registrations}</td>
              <td>{STATUS_VI[e.status]}</td><td><button className="secondary" onClick={() => setOpen(open === e.id ? null : e.id)}>{open === e.id ? "Đóng" : "Sửa"}</button></td></tr>
          ))}</tbody>
        </table></div>
      </Card>
      {open && exams.data && qs.data && (() => {
        const ex = exams.data.find((x: any) => x.id === open);
        return ex ? <ExamEditor key={ex.id + ":" + ex.question_ids.join(",")} exam={ex} questions={qs.data} onChange={exams.reload} /> : null;
      })()}
    </>
  );
}

function Users() {
  const { data, reload } = useLoad(() => api("staff", "/api/admin/users"));
  const [f, setF] = useState({ username: "", password: "", role: "proctor", full_name: "" });
  const [err, setErr] = useState("");
  const create = async () => {
    try { await api("staff", "/api/admin/users", { method: "POST", json: f }); setF({ ...f, username: "", password: "" }); reload(); setErr(""); }
    catch (e: any) { setErr(e.message); }
  };
  return (
    <Card title="Tài khoản">
      <div className="row">
        <div><label>Tên đăng nhập</label><input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></div>
        <div><label>Mật khẩu (≥ 8 ký tự)</label><input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></div>
        <div><label>Họ tên</label><input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></div>
        <div><label>Vai trò</label><select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="proctor">Giám thị</option><option value="admin">Quản trị</option></select></div>
        <button className="shrink" onClick={create}>Tạo</button>
      </div>
      <Err msg={err} />
      <table><thead><tr><th>#</th><th>Tên</th><th>Vai trò</th><th>Họ tên</th></tr></thead>
        <tbody>{(data || []).map((u: any) => <tr key={u.id}><td>{u.id}</td><td>{u.username}</td><td>{u.role}</td><td>{u.full_name}</td></tr>)}</tbody></table>
    </Card>
  );
}

function Audit() {
  const { data } = useLoad(() => api("staff", "/api/admin/audit"));
  return (
    <Card title="Nhật ký truy cập (mọi lần xem bằng chứng, xuất dữ liệu, rà soát)">
      <div className="table-wrap"><table>
        <thead><tr><th>Thời gian</th><th>Người</th><th>Hành động</th><th>Đối tượng</th><th>Chi tiết</th><th>IP</th></tr></thead>
        <tbody>{(data || []).map((a: any) => (
          <tr key={a.id}><td>{fmtTime(a.created_at)}</td><td>{a.user || "–"}</td><td>{a.action}</td><td>{a.target}</td><td className="muted">{a.detail}</td><td>{a.ip}</td></tr>
        ))}</tbody>
      </table></div>
    </Card>
  );
}

export default function AdminPage() {
  const staff = useStaff();
  const [tab, setTab] = useState("exams");
  if (!staff) return null;
  if (staff.role !== "admin") return <><StaffHeader /><main><div className="alert bad">Chỉ quản trị viên.</div></main></>;
  const tabs: [string, string][] = [["exams", "Kỳ thi & thí sinh"], ["questions", "Ngân hàng câu hỏi"], ["users", "Tài khoản"], ["audit", "Nhật ký"]];
  return (
    <>
      <StaffHeader />
      <main>
        <div className="tabs">{tabs.map(([k, l]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
        {tab === "questions" && <Questions />}
        {tab === "exams" && <Exams />}
        {tab === "users" && <Users />}
        {tab === "audit" && <Audit />}
      </main>
    </>
  );
}
