import { api, apiBlob } from "../../api";
import { ExamSelect, useExamPick } from "../../components/ExamPicker";
import { Card, Err, StaffHeader, useLoad, useStaff } from "../../components/ui";

const pct = (x: number | null | undefined) => (x === null || x === undefined ? "–" : `${Math.round(x * 1000) / 10}%`);

export default function Metrics() {
  const staff = useStaff();
  const [examId, pick, exams] = useExamPick();
  const m = useLoad(() => (examId ? api("staff", `/api/proctor/exams/${examId}/metrics`) : Promise.resolve(null)), [examId]);
  const sys = useLoad(() => api("staff", "/api/analysis/system"));
  if (!staff) return null;
  const d = m.data;
  const exportCsv = async () => {
    const b = await apiBlob(`/api/proctor/exams/${examId}/export.csv`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = `features_exam${examId}.csv`;
    a.click();
  };
  return (
    <>
      <StaffHeader />
      <main>
        <Card title="Thống kê đánh giá (số liệu tổng hợp)" actions={<button className="secondary" onClick={exportCsv} disabled={!examId}>Xuất đặc trưng + nhãn (giả danh)</button>}>
          <ExamSelect id={examId} pick={pick} exams={exams} />
          <Err msg={m.error} />
          {d && (
            <>
              <div className="kpis" style={{ marginTop: 12 }}>
                <div className="kpi"><span className="muted">Phiên thi</span><b>{d.attempts}</b></div>
                <div className="kpi"><span className="muted">Phiên bị gắn cờ</span><b>{d.flagged_attempts}</b></div>
                <div className="kpi"><span className="muted">Cờ / đã xác nhận / bác</span><b>{d.flags} / {d.flags_by_status.confirmed || 0} / {d.flags_by_status.dismissed || 0}</b></div>
                <div className="kpi"><span className="muted">Tỉ lệ cờ đúng</span><b>{pct(d.flag_precision)}</b></div>
                <div className="kpi"><span className="muted">Gian lận trong mẫu không cờ</span><b>{pct(d.unflagged_cheating_rate)}</b><span className="muted">n = {d.unflagged_sample_size}</span></div>
                <div className="kpi"><span className="muted">Đồng thuận giám thị (κ)</span><b>{d.reviewer_kappa ?? "–"}</b><span className="muted">{d.kappa_pairs} phiên</span></div>
              </div>
              <h3>Theo hạng máy (công bằng thiết bị)</h3>
              <table><thead><tr><th>Hạng</th><th>Phiên</th><th>Tỉ lệ bị gắn cờ</th><th>Cờ xác nhận</th><th>Cờ bị bác</th><th>Tỉ lệ cờ sai</th></tr></thead>
                <tbody>{Object.entries(d.by_tier).map(([t, v]: [string, any]) => (
                  <tr key={t}><td>{t}</td><td>{v.attempts}</td><td>{pct(v.flag_rate)}</td><td>{v.confirmed_flags}</td><td>{v.dismissed_flags}</td><td>{pct(v.false_flag_share)}</td></tr>
                ))}</tbody></table>
            </>
          )}
        </Card>
        <Card title="Hệ thống (pipeline server)">
          {sys.data && (
            <div className="kpis">
              <div className="kpi"><span className="muted">Chấm rủi ro</span><b>{sys.data.scorer === "fusion" ? "Mô hình hợp nhất" : "Luật ngưỡng"}</b></div>
              <div className="kpi"><span className="muted">YOLO</span><b>{sys.data.yolo ? "Bật" : "Chưa có mô hình"}</b></div>
              <div className="kpi"><span className="muted">Hàng đợi</span><b>{sys.data.bus.kind}</b></div>
              <div className="kpi"><span className="muted">Độ trễ p50 / p95</span><b>{sys.data.latency_p50 ?? "–"} / {sys.data.latency_p95 ?? "–"} s</b></div>
              <div className="kpi"><span className="muted">Khung đã xử lý</span><b>{(sys.data.frames_processed.hi || 0) + (sys.data.frames_processed.lo || 0)}</b></div>
              <div className="kpi"><span className="muted">Khung bị bỏ (quá tải)</span><b>{sys.data.bus.frames_dropped ?? "–"}</b></div>
            </div>
          )}
        </Card>
      </main>
    </>
  );
}
