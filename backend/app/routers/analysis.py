"""Sau ca thi: phân tích thông đồng, truy vết đề bị lộ, thống kê hệ thống."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Answer, Attempt, Candidate, Exam, PaperItem, Question, Registration, User
from ..security import audit, require_admin, require_staff
from ..services import collusion
from ..services.paper import find_watermarks, render_item, trace_leak

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _exam_attempts(db: Session, exam_id: int):
    return db.execute(select(Attempt, Candidate).join(Registration, Registration.id == Attempt.registration_id)
                      .join(Candidate, Candidate.id == Registration.candidate_id)
                      .where(Registration.exam_id == exam_id, Attempt.started_at.is_not(None))).all()


@router.post("/exams/{exam_id}/collusion")
def run_collusion(exam_id: int, request: Request, z_threshold: float | None = None, user: User = Depends(require_staff),
                  db: Session = Depends(get_db)):
    rows = _exam_attempts(db, exam_id)
    who = {a.id: {"pseudonym": a.pseudonym, "candidate_code": c.code, "full_name": c.full_name} for a, c in rows}
    responses = []
    for a, _ in rows:
        ans = db.scalars(select(Answer).where(Answer.attempt_id == a.id)).all()
        responses.append(collusion.Response(
            attempt_id=a.id,
            answers={x.question_id: collusion.normalize_value(x.value_json) for x in ans if x.is_correct is not None},
            correct={x.question_id: bool(x.is_correct) for x in ans if x.is_correct is not None},
            times={x.question_id: x.first_answer_t for x in ans}))
    res = collusion.analyze(responses, z_threshold=z_threshold)
    for p in res["pairs"]:
        p["a_info"], p["b_info"] = who[p["a"]], who[p["b"]]
    for g in res["groups"]:
        g["members_info"] = [who[m] for m in g["members"]]
    audit(db, request, user, "collusion_analysis", str(exam_id), json.dumps(res["stats"]))
    return res


class LeakIn(BaseModel):
    text: str = Field(..., min_length=10, max_length=200_000)


@router.post("/exams/{exam_id}/leak-trace")
def leak_trace(exam_id: int, body: LeakIn, request: Request, user: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    """Từ ảnh chụp đã OCR / bản chép đề bị lộ -> watermark + dấu vân tay nội dung -> thí sinh khả nghi."""
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(404, "Không tìm thấy kỳ thi.")
    rows = _exam_attempts(db, exam_id)
    who = {a.id: {"pseudonym": a.pseudonym, "candidate_code": c.code, "full_name": c.full_name, "watermark": a.watermark}
           for a, c in rows}
    qcache: dict[int, Question] = {}
    papers = {}
    for a, _ in rows:
        items = db.scalars(select(PaperItem).where(PaperItem.attempt_id == a.id).order_by(PaperItem.position)).all()
        rendered = []
        for it in items:
            q = qcache.get(it.question_id) or db.get(Question, it.question_id)
            qcache[it.question_id] = q
            rendered.append(render_item(it, q))
        papers[a.id] = rendered
    wm_hits = []
    marks = find_watermarks(body.text)
    for aid, info in who.items():
        if info["watermark"] in marks:
            wm_hits.append({"attempt_id": aid, **info})
    ranking = trace_leak(body.text, papers)[:20]
    for r in ranking:
        r.update(who[r["attempt_id"]])
    audit(db, request, user, "leak_trace", exam.code, f"watermarks={marks} top={[r['pseudonym'] for r in ranking[:3]]}")
    return {"watermarks_found": marks, "watermark_matches": wm_hits, "content_ranking": ranking,
            "note": "Điểm nội dung dựa trên thứ tự câu, thứ tự phương án, giá trị tham số và cách diễn đạt riêng của từng đề."}


@router.get("/system")
def system_stats(request: Request, _: User = Depends(require_staff)):
    return request.app.state.pipeline.stats()
