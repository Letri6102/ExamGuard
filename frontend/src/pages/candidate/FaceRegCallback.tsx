import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../api";
import { Err } from "../../components/ui";

/** FaceReg chuyển về đây sau check-in: /exam/facereg-callback?ftoken=... */
export default function FaceRegCallback() {
  const [q] = useSearchParams();
  const nav = useNavigate();
  const [err, setErr] = useState("");
  useEffect(() => {
    const ftoken = q.get("ftoken") || q.get("token") || "";
    if (!ftoken) { setErr("Thiếu mã check-in từ FaceReg."); return; }
    api("candidate", "/api/exam/facereg/callback", { method: "POST", json: { ftoken } })
      .then(() => nav("/exam/check", { replace: true }))
      .catch((e) => setErr(e.status === 401 ? "Phiên đăng nhập đã hết, hãy đăng nhập lại (cùng trình duyệt)." : e.message));
  }, [q, nav]);
  return <main style={{ maxWidth: 520 }}><div className="card"><h2>Đang xác nhận kết quả xác thực khuôn mặt…</h2><Err msg={err} /></div></main>;
}
