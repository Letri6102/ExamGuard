"""Quản trị: ngân hàng câu hỏi, kỳ thi, ca thi, danh sách thí sinh, tài khoản giám thị, nhật ký truy cập."""
from __future__ import annotations

import csv
import io
import json
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Attempt, AuditLog, Candidate, Exam, ExamQuestion, Question, Registration, Shift, User,
)
from ..security import audit, hash_password, random_code, require_admin
from ..services.paper import render_text, safe_eval, sample_params

router = APIRouter(prefix="/api/admin", tags=["admin"])
QTYPES = ("single", "multi", "essay", "numeric")


# ------------------------------------------------------------------ câu hỏi
class QuestionIn(BaseModel):
    bank: str = ""
    type: str = Field("single", pattern="^(single|multi|essay|numeric)$")
    stem: str = Field(..., min_length=1, max_length=20000)
    variants: list[str] = []
    options: list = []
    answer: list[str] | str | None = None
    params: dict = {}
    tolerance: float = 1e-6
    difficulty: float = 0.0
    expected_seconds: float = 60.0
    points: float = 1.0


def _normalize_question(q: QuestionIn) -> dict:
    opts = []
    for i, o in enumerate(q.options):
        if isinstance(o, str):
            opts.append({"id": chr(65 + i), "text": o})
        elif isinstance(o, dict) and "text" in o:
            opts.append({"id": str(o.get("id") or chr(65 + i)), "text": str(o["text"])})
        else:
            raise HTTPException(422, "Phương án phải là chuỗi hoặc {id, text}.")
    answer = q.answer
    if q.type in ("single", "multi"):
        if len(opts) < 2:
            raise HTTPException(422, "Câu trắc nghiệm cần ít nhất 2 phương án.")
        answer = [answer] if isinstance(answer, str) else list(answer or [])
        ids = {o["id"] for o in opts}
        if not answer or any(a not in ids for a in answer) or (q.type == "single" and len(answer) != 1):
            raise HTTPException(422, "Đáp án không khớp id phương án.")
    elif q.type == "numeric":
        if not isinstance(answer, str) or not answer:
            raise HTTPException(422, "Câu số cần đáp án là biểu thức, ví dụ 'a + b'.")
    # kiểm tra mẫu tham số có tính được không
    try:
        import random

        params = sample_params(q.params, random.Random(0))
        for text in [q.stem, *q.variants, *[o["text"] for o in opts]]:
            render_text(text, params)
        if q.type == "numeric":
            safe_eval(answer, params)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Tham số/biểu thức lỗi: {e}") from e
    return dict(bank=q.bank, qtype=q.type, stem=q.stem, variants_json=json.dumps(q.variants, ensure_ascii=False),
                options_json=json.dumps(opts, ensure_ascii=False), answer_json=json.dumps(answer, ensure_ascii=False),
                params_json=json.dumps(q.params), tolerance=q.tolerance, difficulty=q.difficulty,
                expected_seconds=q.expected_seconds, points=q.points)


def question_out(q: Question) -> dict:
    return {"id": q.id, "bank": q.bank, "type": q.qtype, "stem": q.stem, "variants": json.loads(q.variants_json),
            "options": json.loads(q.options_json), "answer": json.loads(q.answer_json), "params": json.loads(q.params_json),
            "tolerance": q.tolerance, "difficulty": q.difficulty, "expected_seconds": q.expected_seconds, "points": q.points}


@router.get("/questions")
def list_questions(bank: str = "", q: str = "", _: User = Depends(require_admin), db: Session = Depends(get_db)):
    stmt = select(Question).order_by(Question.id.desc())
    if bank:
        stmt = stmt.where(Question.bank == bank)
    if q:
        stmt = stmt.where(Question.stem.contains(q))
    return [question_out(x) for x in db.scalars(stmt.limit(1000))]


@router.post("/questions")
def create_question(body: QuestionIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    q = Question(**_normalize_question(body))
    db.add(q)
    db.commit()
    return question_out(q)


@router.put("/questions/{qid}")
def update_question(qid: int, body: QuestionIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    q = db.get(Question, qid)
    if not q:
        raise HTTPException(404, "Không tìm thấy câu hỏi.")
    for k, v in _normalize_question(body).items():
        setattr(q, k, v)
    db.commit()
    return question_out(q)


@router.delete("/questions/{qid}")
def delete_question(qid: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    db.execute(delete(Question).where(Question.id == qid))
    db.commit()
    return {"ok": True}


@router.post("/questions/import")
async def import_questions(file: UploadFile = File(...), _: User = Depends(require_admin), db: Session = Depends(get_db)):
    """JSON: danh sách QuestionIn. CSV: type,stem,A,B,C,D,answer,difficulty,expected_seconds,bank."""
    raw = (await file.read()).decode("utf-8-sig")
    items: list[QuestionIn] = []
    if (file.filename or "").lower().endswith(".json") or raw.lstrip().startswith("["):
        items = [QuestionIn(**x) for x in json.loads(raw)]
    else:
        for row in csv.DictReader(io.StringIO(raw)):
            opts = [row[c] for c in "ABCDEFGH" if row.get(c)]
            t = (row.get("type") or "single").strip()
            ans = row.get("answer", "").strip()
            items.append(QuestionIn(type=t, stem=row["stem"], options=opts,
                                    answer=[a.strip() for a in ans.split("|")] if t in ("single", "multi") else (ans or None),
                                    difficulty=float(row.get("difficulty") or 0), bank=row.get("bank", ""),
                                    expected_seconds=float(row.get("expected_seconds") or 60)))
    created = []
    for it in items:
        q = Question(**_normalize_question(it))
        db.add(q)
        created.append(q)
    db.commit()
    return {"created": len(created)}


# ------------------------------------------------------------------ kỳ thi
class ExamIn(BaseModel):
    code: str = Field(..., pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    title: str = Field(..., max_length=256)
    duration_minutes: int = Field(60, ge=1, le=600)
    per_question_seconds: int | None = Field(None, ge=10, le=3600)
    shuffle_questions: bool = True
    shuffle_options: bool = True
    use_variants: bool = True
    allow_back: bool = True
    require_facereg: bool = True


def exam_out(e: Exam, db: Session) -> dict:
    n_reg = db.scalar(select(func.count()).select_from(Registration).where(Registration.exam_id == e.id))
    return {"id": e.id, "code": e.code, "title": e.title, "duration_minutes": e.duration_minutes,
            "per_question_seconds": e.per_question_seconds, "shuffle_questions": e.shuffle_questions,
            "shuffle_options": e.shuffle_options, "use_variants": e.use_variants, "allow_back": e.allow_back,
            "require_facereg": e.require_facereg, "status": e.status,
            "question_ids": [x.question_id for x in e.questions], "registrations": n_reg}


@router.get("/exams")
def list_exams(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [exam_out(e, db) for e in db.scalars(select(Exam).order_by(Exam.id.desc()))]


@router.post("/exams")
def create_exam(body: ExamIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.scalar(select(Exam.id).where(Exam.code == body.code)):
        raise HTTPException(409, "Mã kỳ thi đã tồn tại.")
    e = Exam(**body.model_dump())
    db.add(e)
    db.commit()
    return exam_out(e, db)


@router.put("/exams/{exam_id}")
def update_exam(exam_id: int, body: ExamIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    e = db.get(Exam, exam_id)
    if not e:
        raise HTTPException(404, "Không tìm thấy kỳ thi.")
    for k, v in body.model_dump().items():
        setattr(e, k, v)
    db.commit()
    return exam_out(e, db)


class ExamQuestionsIn(BaseModel):
    question_ids: list[int]


@router.put("/exams/{exam_id}/questions")
def set_exam_questions(exam_id: int, body: ExamQuestionsIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    e = db.get(Exam, exam_id)
    if not e:
        raise HTTPException(404, "Không tìm thấy kỳ thi.")
    if db.scalar(select(func.count()).select_from(Attempt).join(Registration).where(
            Registration.exam_id == exam_id, Attempt.started_at.is_not(None))):
        raise HTTPException(409, "Đã có thí sinh bắt đầu làm bài, không đổi được đề.")
    found = set(db.scalars(select(Question.id).where(Question.id.in_(body.question_ids))))
    e.questions.clear()
    db.flush()
    for i, qid in enumerate(q for q in body.question_ids if q in found):
        e.questions.append(ExamQuestion(question_id=qid, position=i))
    db.commit()
    return exam_out(e, db)


class StatusIn(BaseModel):
    status: str = Field(..., pattern="^(draft|open|closed)$")


@router.post("/exams/{exam_id}/status")
def set_exam_status(exam_id: int, body: StatusIn, request: Request, user: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    e = db.get(Exam, exam_id)
    if not e:
        raise HTTPException(404, "Không tìm thấy kỳ thi.")
    if body.status == "open" and not e.questions:
        raise HTTPException(409, "Kỳ thi chưa có câu hỏi.")
    e.status = body.status
    db.commit()
    audit(db, request, user, "exam_status", e.code, body.status)
    return exam_out(e, db)


class ShiftIn(BaseModel):
    name: str
    starts_at: float
    ends_at: float


@router.get("/exams/{exam_id}/shifts")
def list_shifts(exam_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": s.id, "name": s.name, "starts_at": s.starts_at, "ends_at": s.ends_at}
            for s in db.scalars(select(Shift).where(Shift.exam_id == exam_id).order_by(Shift.starts_at))]


@router.post("/exams/{exam_id}/shifts")
def create_shift(exam_id: int, body: ShiftIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = Shift(exam_id=exam_id, **body.model_dump())
    db.add(s)
    db.commit()
    return {"id": s.id}


# ------------------------------------------------------------------ thí sinh
@router.post("/exams/{exam_id}/candidates/import")
async def import_candidates(exam_id: int, file: UploadFile = File(...), shift: str = Form(""),
                            _: User = Depends(require_admin), db: Session = Depends(get_db)):
    """CSV: code,full_name[,email][,shift]. Mã dự thi được tạo ngẫu nhiên (xuất CSV để in giấy báo)."""
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(404, "Không tìm thấy kỳ thi.")
    shifts = {s.name: s.id for s in db.scalars(select(Shift).where(Shift.exam_id == exam_id))}
    raw = (await file.read()).decode("utf-8-sig")
    added = updated = 0
    for row in csv.DictReader(io.StringIO(raw)):
        code = (row.get("code") or row.get("ma") or "").strip()
        if not code:
            continue
        cand = db.scalar(select(Candidate).where(Candidate.code == code))
        if not cand:
            cand = Candidate(code=code)
            db.add(cand)
        cand.full_name = (row.get("full_name") or row.get("ho_ten") or cand.full_name or "").strip()
        cand.email = (row.get("email") or cand.email or "").strip()
        db.flush()
        reg = db.scalar(select(Registration).where(Registration.exam_id == exam_id, Registration.candidate_id == cand.id))
        shift_id = shifts.get((row.get("shift") or shift).strip())
        if reg:
            reg.shift_id = shift_id or reg.shift_id
            updated += 1
        else:
            db.add(Registration(exam_id=exam_id, candidate_id=cand.id, shift_id=shift_id, access_code=random_code(6)))
            added += 1
    db.commit()
    return {"added": added, "updated": updated}


@router.get("/exams/{exam_id}/candidates")
def list_registrations(exam_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(Registration, Candidate, Attempt).join(Candidate, Candidate.id == Registration.candidate_id)
                      .outerjoin(Attempt, Attempt.registration_id == Registration.id)
                      .where(Registration.exam_id == exam_id).order_by(Candidate.code)).all()
    return [{"registration_id": r.id, "code": c.code, "full_name": c.full_name, "email": c.email,
             "access_code": r.access_code, "shift_id": r.shift_id,
             "attempt": {"id": a.id, "status": a.status, "score": a.score} if a else None} for r, c, a in rows]


@router.get("/exams/{exam_id}/candidates.csv")
def export_roster(exam_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    exam = db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(404, "Không tìm thấy kỳ thi.")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["exam_code", "code", "full_name", "access_code"])
    for r in list_registrations(exam_id, user, db):
        w.writerow([exam.code, r["code"], r["full_name"], r["access_code"]])
    audit(db, request, user, "export_roster", exam.code)
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{exam.code}_thi_sinh.csv"'})


# ------------------------------------------------------------------ tài khoản & nhật ký
class UserIn(BaseModel):
    username: str = Field(..., pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    password: str = Field(..., min_length=8, max_length=128)
    role: str = Field("proctor", pattern="^(admin|proctor)$")
    full_name: str = ""


@router.get("/users")
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": u.id, "username": u.username, "role": u.role, "full_name": u.full_name}
            for u in db.scalars(select(User).order_by(User.id))]


@router.post("/users")
def create_user(body: UserIn, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "Tên đăng nhập đã tồn tại.")
    u = User(username=body.username, password_hash=hash_password(body.password), role=body.role, full_name=body.full_name)
    db.add(u)
    db.commit()
    audit(db, request, user, "create_user", body.username, body.role)
    return {"id": u.id}


@router.get("/audit")
def audit_log(limit: int = 300, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(AuditLog, User.username).outerjoin(User, User.id == AuditLog.user_id)
                      .order_by(AuditLog.id.desc()).limit(min(limit, 2000))).all()
    return [{"id": a.id, "user": u, "action": a.action, "target": a.target, "detail": a.detail, "ip": a.ip,
             "created_at": a.created_at} for a, u in rows]


@router.post("/maintenance/purge-evidence")
def purge_evidence(request: Request, user: User = Depends(require_admin)):
    """Xóa khung bằng chứng quá hạn lưu (EVIDENCE_RETENTION_DAYS)."""
    s = request.app.state.settings
    cutoff = time.time() - s.evidence_retention_days * 86400
    n = 0
    for p in s.evidence_dir.glob("attempt_*/*.jpg"):
        if p.stat().st_mtime < cutoff:
            p.unlink()
            n += 1
    return {"deleted": n}
