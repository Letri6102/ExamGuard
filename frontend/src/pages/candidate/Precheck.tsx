import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, auth } from "../../api";
import { Err } from "../../components/ui";
import { getStream, getTracker } from "../../media";

type Check = { key: string; label: string; state: "pending" | "ok" | "warn" | "bad"; note?: string };

function browserInfo(): { ok: boolean; name: string } {
  const brands: { brand: string }[] = (navigator as any).userAgentData?.brands || [];
  const names = brands.map((b) => b.brand).join(", ");
  const chromium = /Chrom|Edge/i.test(names) || /Chrome\/|Edg\//.test(navigator.userAgent);
  return { ok: chromium, name: names || navigator.userAgent.split(" ").slice(-2).join(" ") };
}

export default function Precheck() {
  const nav = useNavigate();
  const video = useRef<HTMLVideoElement>(null);
  const [st, setSt] = useState<any>(null);
  const [checks, setChecks] = useState<Check[]>([]);
  const [err, setErr] = useState("");
  const [level, setLevel] = useState(0);
  const [busy, setBusy] = useState(false);

  const set = (c: Check) => setChecks((prev) => [...prev.filter((x) => x.key !== c.key), c]);

  const loadState = async () => {
    const s = await api("candidate", "/api/exam/state");
    if (s.attempt.status === "in_progress") nav("/exam/take", { replace: true });
    if (s.attempt.status === "submitted") nav("/exam/done", { replace: true });
    setSt(s);
    return s;
  };

  useEffect(() => {
    if (!auth.token("candidate")) { nav("/exam/login"); return; }
    let audioCtx: AudioContext | null = null;
    let raf = 0;
    (async () => {
      try {
        await loadState();
      } catch (e: any) {
        setErr(e.message);
        return;
      }
      const b = browserInfo();
      set({ key: "browser", label: "Trình duyệt", state: b.ok ? "ok" : "warn",
        note: b.ok ? b.name : "Nên dùng Chrome hoặc Edge bản mới (một số kiểm tra chỉ chạy trên Chromium)." });
      const t0 = performance.now();
      try {
        await api(null, "/api/health");
        const ms = Math.round(performance.now() - t0);
        set({ key: "net", label: "Kết nối máy chủ", state: ms < 800 ? "ok" : "warn", note: `${ms} ms` });
      } catch {
        set({ key: "net", label: "Kết nối máy chủ", state: "bad", note: "Không kết nối được" });
      }
      const scr: any = window.screen;
      if (typeof scr.isExtended === "boolean") {
        set({ key: "screen", label: "Màn hình", state: scr.isExtended ? "warn" : "ok",
          note: scr.isExtended ? "Phát hiện màn hình mở rộng: hãy tháo màn hình phụ trước khi thi (không bắt buộc, nhưng sẽ được ghi nhận)." : "Một màn hình" });
      } else set({ key: "screen", label: "Màn hình", state: "warn", note: "Trình duyệt không cho kiểm tra màn hình phụ." });
      set({ key: "fs", label: "Toàn màn hình", state: document.fullscreenEnabled ? "ok" : "warn",
        note: document.fullscreenEnabled ? "Hỗ trợ" : "Không hỗ trợ" });
      let stream: MediaStream;
      try {
        stream = await getStream();
        if (video.current) { video.current.srcObject = stream; await video.current.play().catch(() => {}); }
        set({ key: "cam", label: "Camera", state: stream.getVideoTracks().length ? "ok" : "bad",
          note: stream.getVideoTracks()[0]?.label || "" });
        const hasMic = stream.getAudioTracks().length > 0;
        set({ key: "mic", label: "Micro", state: hasMic ? "ok" : "warn", note: hasMic ? "Nói thử để thấy mức âm thanh" : "Không có micro" });
        if (hasMic) {
          audioCtx = new AudioContext();
          const an = audioCtx.createAnalyser();
          audioCtx.createMediaStreamSource(stream).connect(an);
          const buf = new Float32Array(an.fftSize);
          const tick = () => {
            an.getFloatTimeDomainData(buf);
            setLevel(Math.sqrt(buf.reduce((s, x) => s + x * x, 0) / buf.length));
            raf = requestAnimationFrame(tick);
          };
          tick();
        }
      } catch (e: any) {
        set({ key: "cam", label: "Camera / micro", state: "bad", note: "Chưa cấp quyền camera/micro: " + (e.message || e) });
        return;
      }
      set({ key: "face", label: "Nhận diện khuôn mặt trên máy", state: "pending", note: "Đang tải mô hình…" });
      try {
        const tracker = await getTracker();
        await new Promise((r) => setTimeout(r, 800));
        const ms = tracker.benchmark(video.current!, 5);
        const faces = tracker.detect(video.current!);
        set({ key: "face", label: "Nhận diện khuôn mặt trên máy", state: faces.length === 1 ? "ok" : "warn",
          note: faces.length === 1 ? `1 khuôn mặt · ${ms.toFixed(1)} ms/lần` : `${faces.length} khuôn mặt — hãy ngồi một mình, đủ sáng, nhìn vào camera` });
        const d = await api("candidate", "/api/exam/device", { method: "POST", json: {
          cores: navigator.hardwareConcurrency || 0, memory_gb: (navigator as any).deviceMemory || 0, face_ms: ms,
          user_agent: navigator.userAgent.slice(0, 400), screen: { w: screen.width, h: screen.height, dpr: devicePixelRatio },
          extended_screen: typeof scr.isExtended === "boolean" ? scr.isExtended : null,
        } });
        set({ key: "tier", label: "Hạng máy", state: "ok", note: { low: "Máy yếu: giám sát chạy ở chế độ nhẹ", mid: "Trung bình", high: "Mạnh" }[d.tier as string] });
      } catch (e: any) {
        set({ key: "face", label: "Nhận diện khuôn mặt trên máy", state: "bad", note: "Không tải được mô hình: " + (e.message || e) });
      }
    })();
    return () => { cancelAnimationFrame(raf); audioCtx?.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const order = ["browser", "net", "cam", "mic", "face", "tier", "screen", "fs"];
  const sorted = [...checks].sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
  const blocking = checks.some((c) => c.state === "bad" && ["cam", "net", "face"].includes(c.key)) || !checks.find((c) => c.key === "face" && c.state !== "pending");
  const fr = st?.facereg;
  const verified = st?.attempt.status === "verified";

  const goFaceReg = () => { window.location.href = fr.checkin_url; };
  const skip = async () => { try { await api("candidate", "/api/exam/facereg/skip", { method: "POST" }); await loadState(); } catch (e: any) { setErr(e.message); } };
  const refresh = async () => { try { await api("candidate", "/api/exam/facereg/refresh", { method: "POST" }); await loadState(); } catch (e: any) { setErr(e.message); } };
  const start = async () => {
    setBusy(true);
    try {
      await api("candidate", "/api/exam/start", { method: "POST" });
      nav("/exam/take");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main>
      <div className="grid2">
        <div className="card">
          <h2>{st ? `${st.exam.title} (${st.exam.code})` : "Kiểm tra trước giờ thi"}</h2>
          {st && <p className="muted">Thí sinh: <b>{st.candidate.full_name}</b> · SBD {st.candidate.code} · {st.exam.total_questions} câu · {st.exam.duration_minutes} phút</p>}
          <video ref={video} className="cam-preview" muted playsInline />
          <label>Mức âm thanh micro</label>
          <meter min={0} max={0.2} value={level} style={{ width: "100%" }} />
        </div>
        <div className="card">
          <h2>Kiểm tra thiết bị</h2>
          <ul className="checklist">
            {sorted.map((c) => (
              <li key={c.key}>
                <span className={`badge ${c.state === "ok" ? "ok" : c.state === "bad" ? "bad" : "warn"}`}>
                  {c.state === "ok" ? "Đạt" : c.state === "bad" ? "Lỗi" : c.state === "pending" ? "…" : "Lưu ý"}
                </span>
                <div><b>{c.label}</b><div className="muted">{c.note}</div></div>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="card">
        <h2>Xác thực khuôn mặt</h2>
        {!st ? null : verified ? (
          <div className="alert ok">Đã xác thực {st.attempt.facereg_status === "SKIPPED" ? "(kỳ thi không yêu cầu xác thực khuôn mặt)" : "khuôn mặt với ảnh đăng ký"}.</div>
        ) : st.attempt.facereg_status === "REVIEW" ? (
          <><div className="alert warn">Kết quả cần giám thị duyệt. Vui lòng chờ rồi bấm kiểm tra lại.</div><button onClick={refresh}>Kiểm tra lại</button></>
        ) : st.attempt.facereg_status === "REJECT" ? (
          <div className="alert bad">Xác thực không thành công. Hãy liên hệ giám thị.</div>
        ) : fr.required && fr.enabled ? (
          <><p>Bạn sẽ được chuyển sang trang FaceReg để so khớp khuôn mặt với ảnh 4x6 (làm theo hướng dẫn quay đầu), rồi tự động quay lại đây.</p>
            <button onClick={goFaceReg} disabled={blocking}>Xác thực khuôn mặt</button></>
        ) : fr.required ? (
          <div className="alert bad">Kỳ thi yêu cầu xác thực khuôn mặt nhưng máy chủ chưa cấu hình FaceReg. Liên hệ giám thị.</div>
        ) : (
          <><p className="muted">Kỳ thi này không bắt buộc xác thực khuôn mặt.</p><button className="secondary" onClick={skip} disabled={blocking}>Tiếp tục không xác thực</button></>
        )}
        <Err msg={err} />
        <h3>Khi làm bài</h3>
        <ul className="muted">
          <li>Bài thi ở chế độ toàn màn hình, hiển thị từng câu. Không dán nội dung, không sao chép đề.</li>
          <li>Rời tab, thoát toàn màn hình, có người khác, có tiếng nói… đều được <b>ghi nhận</b> để giám thị xem xét (không tự khóa bài).</li>
          <li>Mất mạng: cứ tiếp tục làm, bài làm được lưu trên máy và tự gửi lại khi có mạng.</li>
        </ul>
        <button onClick={start} disabled={!verified || busy || blocking}>Bắt đầu làm bài</button>
      </div>
    </main>
  );
}
