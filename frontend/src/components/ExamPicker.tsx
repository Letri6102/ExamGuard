import { useEffect, useState } from "react";
import { api } from "../api";

export function useExamPick(): [number | null, (id: number) => void, any[]] {
  const [exams, setExams] = useState<any[]>([]);
  const [id, setId] = useState<number | null>(() => Number(sessionStorage.getItem("eg_exam")) || null);
  useEffect(() => {
    api("staff", "/api/proctor/exams").then((list) => {
      setExams(list);
      if (list.length && !list.some((e: any) => e.id === id)) setId(list[0].id);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const pick = (v: number) => { setId(v); try { sessionStorage.setItem("eg_exam", String(v)); } catch { /* bỏ qua */ } };
  return [id, pick, exams];
}

export function ExamSelect({ id, pick, exams }: { id: number | null; pick: (id: number) => void; exams: any[] }) {
  if (!exams.length) return <div className="alert warn">Chưa có kỳ thi nào đang mở hoặc đã đóng.</div>;
  return (
    <div style={{ maxWidth: 420 }}>
      <label htmlFor="exam-pick">Kỳ thi</label>
      <select id="exam-pick" value={id ?? ""} onChange={(e) => pick(Number(e.target.value))}>
        {exams.map((e) => <option key={e.id} value={e.id}>{e.code} · {e.title} ({e.in_progress} đang thi / {e.attempts})</option>)}
      </select>
    </div>
  );
}
