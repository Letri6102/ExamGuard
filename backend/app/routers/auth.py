from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Attempt, Candidate, Exam, Registration, User
from ..security import audit, current_user, make_token, random_code, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=256)


class CandidateLoginIn(BaseModel):
    exam_code: str = Field(..., max_length=64)
    candidate_code: str = Field(..., max_length=64)
    access_code: str = Field(..., max_length=16)


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username.strip()))
    if not user or not verify_password(body.password, user.password_hash):
        audit(db, request, None, "login_failed", body.username[:64])
        raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu.")
    audit(db, request, user, "login")
    s = request.app.state.settings
    return {"token": make_token(s.secret_key, str(user.id), user.role, 12 * 3600),
            "user": {"id": user.id, "username": user.username, "role": user.role, "full_name": user.full_name}}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "role": user.role, "full_name": user.full_name}


def new_attempt(db: Session, reg: Registration) -> Attempt:
    for _ in range(10):
        pseudo, wm = "P" + random_code(7), random_code(8)
        if not db.scalar(select(Attempt.id).where((Attempt.pseudonym == pseudo) | (Attempt.watermark == wm))):
            att = Attempt(registration_id=reg.id, pseudonym=pseudo, watermark=wm)
            db.add(att)
            db.commit()
            return att
    raise HTTPException(500, "Không tạo được mã phiên.")


@router.post("/candidate-login")
def candidate_login(body: CandidateLoginIn, request: Request, db: Session = Depends(get_db)):
    row = db.execute(
        select(Registration, Exam, Candidate)
        .join(Exam, Exam.id == Registration.exam_id).join(Candidate, Candidate.id == Registration.candidate_id)
        .where(func.upper(Exam.code) == body.exam_code.strip().upper(),
               func.upper(Candidate.code) == body.candidate_code.strip().upper())
    ).first()
    if not row or not secrets.compare_digest(row[0].access_code, body.access_code.strip().upper()):
        raise HTTPException(401, "Sai mã kỳ thi, số báo danh hoặc mã dự thi.")
    reg, exam, cand = row
    if exam.status != "open":
        raise HTTPException(403, "Kỳ thi chưa mở hoặc đã đóng.")
    att = db.scalar(select(Attempt).where(Attempt.registration_id == reg.id)) or new_attempt(db, reg)
    if att.status == "submitted":
        raise HTTPException(403, "Bạn đã nộp bài.")
    s = request.app.state.settings
    ttl = (exam.duration_minutes + 120) * 60
    return {"token": make_token(s.secret_key, str(att.id), "candidate", ttl),
            "attempt": {"id": att.id, "status": att.status}, "exam": {"code": exam.code, "title": exam.title},
            "candidate": {"code": cand.code, "full_name": cand.full_name}}
