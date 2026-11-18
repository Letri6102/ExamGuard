"""Mô hình dữ liệu.

Quyền riêng tư: dữ liệu giám sát (đặc trưng, cờ, sự kiện) chỉ gắn với `Attempt` (có mã giả danh `pseudonym`).
Danh tính thật (tên, mã thí sinh) nằm ở `Candidate`/`Registration`; xuất dữ liệu huấn luyện chỉ dùng mã giả danh.
"""
from __future__ import annotations

import time

from sqlalchemy import (
    Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> float:
    return time.time()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))  # admin | proctor
    full_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)  # trùng số báo danh bên FaceReg
    full_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    bank: Mapped[str] = mapped_column(String(64), default="")
    qtype: Mapped[str] = mapped_column(String(16))           # single | multi | essay | numeric
    stem: Mapped[str] = mapped_column(Text)                  # có thể chứa {{a}} {{b}} (tham số)
    variants_json: Mapped[str] = mapped_column(Text, default="[]")   # các cách diễn đạt khác của đề
    options_json: Mapped[str] = mapped_column(Text, default="[]")    # [{id, text}] (text có thể chứa {{expr}})
    answer_json: Mapped[str] = mapped_column(Text, default="null")   # [id...] | "biểu thức" (numeric) | null (essay)
    params_json: Mapped[str] = mapped_column(Text, default="{}")     # {a: {min, max, step}}
    tolerance: Mapped[float] = mapped_column(Float, default=1e-6)
    difficulty: Mapped[float] = mapped_column(Float, default=0.0)    # tham số b (IRT)
    expected_seconds: Mapped[float] = mapped_column(Float, default=60.0)
    points: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Exam(Base):
    __tablename__ = "exams"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)  # trùng mã kỳ thi bên FaceReg
    title: Mapped[str] = mapped_column(String(256))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    per_question_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shuffle_questions: Mapped[bool] = mapped_column(Boolean, default=True)
    shuffle_options: Mapped[bool] = mapped_column(Boolean, default=True)
    use_variants: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_back: Mapped[bool] = mapped_column(Boolean, default=True)
    require_facereg: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | open | closed
    created_at: Mapped[float] = mapped_column(Float, default=now)
    questions: Mapped[list[ExamQuestion]] = relationship(order_by="ExamQuestion.position", cascade="all, delete-orphan")


class ExamQuestion(Base):
    __tablename__ = "exam_questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)


class Shift(Base):
    __tablename__ = "shifts"
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    starts_at: Mapped[float] = mapped_column(Float)
    ends_at: Mapped[float] = mapped_column(Float)


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (UniqueConstraint("exam_id", "candidate_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"))
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    access_code: Mapped[str] = mapped_column(String(16))  # mã dự thi in trên giấy báo


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    registration_id: Mapped[int] = mapped_column(ForeignKey("registrations.id", ondelete="CASCADE"), unique=True)
    pseudonym: Mapped[str] = mapped_column(String(16), unique=True)
    watermark: Mapped[str] = mapped_column(String(16), unique=True)  # mã hiển thị trên watermark (truy vết lộ đề)
    status: Mapped[str] = mapped_column(String(16), default="precheck")  # precheck|verified|in_progress|submitted
    facereg_token: Mapped[str] = mapped_column(String(128), default="")
    facereg_status: Mapped[str] = mapped_column(String(16), default="")  # ACCEPT | REVIEW | REJECT | SKIPPED
    device_json: Mapped[str] = mapped_column(Text, default="{}")
    device_tier: Mapped[str] = mapped_column(String(8), default="mid")
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    deadline_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    submitted_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_seen_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk: Mapped[float] = mapped_column(Float, default=0.0)
    risk_peak: Mapped[float] = mapped_column(Float, default=0.0)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    review_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class PaperItem(Base):
    __tablename__ = "paper_items"
    __table_args__ = (UniqueConstraint("attempt_id", "position"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    variant_idx: Mapped[int] = mapped_column(Integer, default=0)
    option_perm_json: Mapped[str] = mapped_column(Text, default="[]")  # id phương án gốc theo thứ tự hiển thị
    params_json: Mapped[str] = mapped_column(Text, default="{}")


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    value_json: Mapped[str] = mapped_column(Text)        # ĐÃ quy về đề gốc (id phương án gốc)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    changes: Mapped[int] = mapped_column(Integer, default=0)
    first_answer_t: Mapped[float] = mapped_column(Float, default=0.0)  # giây kể từ lúc bắt đầu làm bài
    last_answer_t: Mapped[float] = mapped_column(Float, default=0.0)
    client_seq: Mapped[int] = mapped_column(Integer, default=0)


class FeatureWindow(Base):
    __tablename__ = "feature_windows"
    __table_args__ = (UniqueConstraint("attempt_id", "t_index"), Index("ix_fw_attempt_t", "attempt_id", "t_index"))
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"))
    t_index: Mapped[int] = mapped_column(Integer)                  # cửa sổ thứ n (mỗi cửa sổ 2 giây)
    values_json: Mapped[str] = mapped_column(Text)                 # 54 giá trị theo shared/feature_schema.json
    masks_json: Mapped[str] = mapped_column(Text)                  # 8 cờ có/không từng phương thức
    risk: Mapped[float] = mapped_column(Float, default=0.0)
    window_score: Mapped[float] = mapped_column(Float, default=0.0)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[float] = mapped_column(Float, default=now)


class SessionEvent(Base):
    __tablename__ = "session_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    t: Mapped[float] = mapped_column(Float)          # giây kể từ lúc bắt đầu
    kind: Mapped[str] = mapped_column(String(32))
    detail_json: Mapped[str] = mapped_column(Text, default="{}")


class IdentityCheck(Base):
    __tablename__ = "identity_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    t: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16))  # OK | MISMATCH | NO_FACE | MULTI_FACE | ERROR
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(32), default="periodic")  # periodic | reappear


class Flag(Base):
    __tablename__ = "flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    start_t: Mapped[float] = mapped_column(Float)
    end_t: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")  # tên file khung mờ mặt (nếu giữ bằng chứng)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | confirmed | dismissed
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    reviewed_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class SessionReview(Base):
    """Rà soát cả phiên (dùng cho mẫu ngẫu nhiên phiên không bị gắn cờ -> ước lượng recall không thiên lệch)."""
    __tablename__ = "session_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    verdict: Mapped[str] = mapped_column(String(16))  # clean | cheating | unsure
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
