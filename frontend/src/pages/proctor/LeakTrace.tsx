import { useState } from "react";
import { api } from "../../api";
import { ExamSelect, useExamPick } from "../../components/ExamPicker";
import { Card, Err, StaffHeader, useStaff } from "../../components/ui";

export default function LeakTrace() {
  const staff = useStaff();
  const [examId, pick, exams] = useExamPick();
  const [text, setText] = useState("");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState("");
  if (!staff) return null;
  const run = async () => {
    try { setRes(await api("staff", `/api/analysis/exams/${examId}/leak-trace`, { method: "POST", json: { text } })); setErr(""); }
    catch (e: any) { setErr(e.message); }
  };
  return (
    <>
      <StaffHeader />
      <main>
        <Card title="Truy vết đề bị lộ">
          <p className="muted">Dán nội dung đề bị lộ (chép tay, hoặc chữ đọc được từ ảnh chụp bằng OCR). Hệ thống tìm mã watermark hiển thị
            và so <b>dấu vân tay nội dung</b> (thứ tự câu, thứ tự phương án, giá trị tham số, cách diễn đạt) với đề riêng của từng thí sinh.</p>
          <ExamSelect id={examId} pick={pick} exams={exams} />
          <label htmlFor="leak">Nội dung bị lộ</label>
          <textarea id="leak" rows={10} value={text} onChange={(e) => setText(e.target.value)} />
          <p><button onClick={run} disabled={!examId || text.length < 10}>Truy vết</button></p>
          <Err msg={err} />
        </Card>
        {res && (
          <Card title="Kết quả">
            <p>Mã watermark tìm thấy: {res.watermarks_found.join(", ") || "không có"}</p>
            {res.watermark_matches.map((m: any) => <div key={m.attempt_id} className="alert bad">Watermark khớp: <b>{m.candidate_code}</b> ({m.full_name}) · {m.pseudonym}</div>)}
            <h3>Xếp hạng theo nội dung</h3>
            <table><thead><tr><th>Thí sinh</th><th>Điểm</th><th>Số câu khớp</th><th>Thành phần</th></tr></thead>
              <tbody>{res.content_ranking.map((r: any) => (
                <tr key={r.attempt_id}><td>{r.candidate_code} ({r.full_name})</td><td>{r.score}</td><td>{r.matched_questions}</td>
                  <td className="muted">{Object.entries(r.components).map(([k, v]) => `${k}: ${v}`).join(" · ")}</td></tr>
              ))}</tbody></table>
          </Card>
        )}
      </main>
    </>
  );
}
