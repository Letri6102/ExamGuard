import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, auth } from "../api";
import { Err } from "../components/ui";

export default function StaffLogin() {
  const nav = useNavigate();
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const r = await api(null, "/api/auth/login", { method: "POST", json: { username: u, password: p } });
      auth.set("staff", r.token, r.user);
      nav(r.user.role === "admin" ? "/admin" : "/proctor");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <main style={{ maxWidth: 420 }}>
      <form className="card" onSubmit={submit}>
        <h2>Giám thị / Quản trị</h2>
        <label htmlFor="u">Tên đăng nhập</label>
        <input id="u" value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" />
        <label htmlFor="p">Mật khẩu</label>
        <input id="p" type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" />
        <Err msg={err} />
        <p><button>Đăng nhập</button></p>
      </form>
    </main>
  );
}
