import { Link } from "react-router-dom";
import { api, fmtSec } from "../../api";
import { ExamSelect, useExamPick } from "../../components/ExamPicker";
import { Card, Err, StaffHeader, useLoad, useStaff } from "../../components/ui";

export default function ReviewQueue() {
  const staff = useStaff();
  const [examId, pick, exams] = useExamPick();
  const { data, error } = useLoad(() => (examId ? api("staff", `/api/proctor/exams/${examId}/review-queue`) : Promise.resolve(null)), [examId]);
  if (!staff) return null;
  return (
    <>
      <StaffHeader />
      <main>
        <Card title="Hàng đợi rà soát"><ExamSelect id={examId} pick={pick} exams={exams} /><Err msg={error} /></Card>
        <Card title={`Cờ đang mở (${data?.flags.length ?? 0})`}>
          <table><thead><tr><th>Thí sinh</th><th>Thời điểm</th><th>Điểm</th><th>Lý do chính</th></tr></thead>
            <tbody>{(data?.flags || []).map((f: any) => (
              <tr key={f.flag_id}><td><Link to={`/proctor/attempt/${f.attempt_id}`}>{f.candidate_code}</Link> <span className="muted">{f.pseudonym}</span></td>
                <td>{fmtSec(f.start_t)}–{fmtSec(f.end_t)}</td><td>{Math.round(f.score)}</td>
                <td className="muted">{f.reasons.slice(0, 3).map((r: any) => r[0]).join(" · ")}</td></tr>
            ))}</tbody></table>
        </Card>
        <Card title={`Mẫu ngẫu nhiên phiên KHÔNG bị gắn cờ (${data?.sample.length ?? 0})`}>
          <p className="muted">Rà soát các phiên này giúp ước lượng tỉ lệ gian lận bị bỏ sót (recall) mà không thiên lệch theo cờ của hệ thống.</p>
          <table><thead><tr><th>Thí sinh</th><th>Điểm rủi ro cao nhất</th></tr></thead>
            <tbody>{(data?.sample || []).map((s: any) => (
              <tr key={s.attempt_id}><td><Link to={`/proctor/attempt/${s.attempt_id}`}>{s.candidate_code}</Link> <span className="muted">{s.pseudonym}</span></td><td>{Math.round(s.peak)}</td></tr>
            ))}</tbody></table>
        </Card>
      </main>
    </>
  );
}
