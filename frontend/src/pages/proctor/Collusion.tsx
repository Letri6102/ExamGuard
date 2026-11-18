import { useState } from "react";
import { api } from "../../api";
import { ExamSelect, useExamPick } from "../../components/ExamPicker";
import { Card, Err, StaffHeader, useStaff } from "../../components/ui";

export default function Collusion() {
  const staff = useStaff();
  const [examId, pick, exams] = useExamPick();
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  if (!staff) return null;
  const run = async () => {
    setBusy(true);
    try { setRes(await api("staff", `/api/analysis/exams/${examId}/collusion`, { method: "POST" })); setErr(""); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const who = (i: any) => `${i.candidate_code} (${i.full_name})`;
  return (
    <>
      <StaffHeader />
      <main>
        <Card title="Phân tích thông đồng sau ca thi">
          <p className="muted">Đáp án được quy về đề gốc. Chỉ số ω so số câu trả lời trùng với kỳ vọng nếu làm độc lập (tính theo năng lực và độ khó);
            ngưỡng hiệu chỉnh theo số cặp. Kèm độ đồng bộ thời gian trả lời. Kết quả là <b>gợi ý</b> cho hội đồng, không phải kết luận.</p>
          <div className="row"><ExamSelect id={examId} pick={pick} exams={exams} /><button className="shrink" onClick={run} disabled={!examId || busy}>{busy ? "Đang chạy…" : "Chạy phân tích"}</button></div>
          <Err msg={err} />
        </Card>
        {res && (
          <>
            <Card title="Tổng quan">
              <p>{res.stats.attempts} phiên · {res.stats.candidate_pairs}/{res.stats.all_pairs} cặp được xét ({res.stats.mode === "all_pairs" ? "mọi cặp" : "lọc trước bằng đáp án sai hiếm"}) ·
                ngưỡng z = {res.stats.z_threshold} · <b>{res.stats.flagged_pairs}</b> cặp vượt ngưỡng</p>
              {res.groups.map((g: any, i: number) => (
                <div key={i} className="alert warn">Nhóm {i + 1} (z tối đa {g.max_z}): {g.members_info.map(who).join(", ")}</div>
              ))}
            </Card>
            <Card title="Các cặp đáng chú ý">
              <div className="table-wrap"><table>
                <thead><tr><th>Cặp</th><th>z (ω)</th><th>Trùng / kỳ vọng</th><th>Sai trùng / cùng sai</th><th>Trả lời cách &lt; 30 s</th><th>DTW</th></tr></thead>
                <tbody>{res.pairs.slice(0, 50).map((p: any) => (
                  <tr key={p.a + "-" + p.b} style={{ fontWeight: p.z >= res.stats.z_threshold ? 700 : 400 }}>
                    <td>{who(p.a_info)}<br />{who(p.b_info)}</td><td>{p.z}</td><td>{p.matches} / {p.expected}</td>
                    <td>{p.identical_wrong} / {p.both_wrong}</td><td>{p.within_30s !== undefined ? `${Math.round(p.within_30s * 100)}%` : "–"}</td><td>{p.dtw ?? "–"}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            </Card>
          </>
        )}
      </main>
    </>
  );
}
