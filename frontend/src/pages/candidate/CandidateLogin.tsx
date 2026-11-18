import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, auth } from "../../api";
import { Err } from "../../components/ui";

export default function CandidateLogin() {
  const nav = useNavigate();
  const [f, setF] = useState({ exam_code: "", candidate_code: "", access_code: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api(null, "/api/auth/candidate-login", { method: "POST", json: f });
      auth.set("candidate", r.token, r);
      nav(r.attempt.status === "in_progress" ? "/exam/take" : "/exam/check");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  const field = (k: keyof typeof f, label: string, hint?: string) => (
    <div>
      <label htmlFor={k}>{label}</label>
      <input id={k} value={f[k]} autoComplete="off" onChange={(e) => setF({ ...f, [k]: e.target.value })} placeholder={hint} />
    </div>
  );
  return (
    <main style={{ maxWidth: 480 }}>
      <form className="card" onSubmit={submit}>
        <h2>Vào phòng thi</h2>
        {field("exam_code", "Mã kỳ thi", "VD: DEMO01")}
        {field("candidate_code", "Số báo danh")}
        {field("access_code", "Mã dự thi (trên giấy báo)")}
        <Err msg={err} />
        <p><button disabled={busy}>Tiếp tục</button></p>
        <p className="muted">Bài thi dùng camera và micro trên máy bạn. Video và âm thanh <b>không</b> được gửi đi; máy chỉ gửi
          các chỉ số tổng hợp, khung hình đã làm mờ khuôn mặt, và ảnh khuôn mặt định kỳ để xác thực danh tính (xóa sau khi xử lý).</p>
      </form>
    </main>
  );
}
