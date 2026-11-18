"""API cho thí sinh: kiểm tra thiết bị, xác thực FaceReg, làm bài từng câu, xác thực lại, gửi đặc trưng."""
from __future__ import annotations

import base64
import binascii
import json
import random
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import db as dbmod
from ..db import get_db
from ..models import Answer, Attempt, Candidate, Exam, IdentityCheck, PaperItem, Question, Registration, SessionEvent
from ..security import current_attempt, decode_token
from ..services.facereg import FaceRegError
from ..services.paper import generate_paper, grade, render_item, to_original

router = APIRouter(prefix="/api/exam", tags=["exam"])
GRACE_SECONDS = 30


def _ctx(db: Session, att: Attempt) -> tuple[Exam, Candidate, Registration]:
    reg = db.get(Registration, att.registration_id)
    return db.get(Exam, reg.exam_id), db.get(Candidate, reg.candidate_id), reg


def _facereg_required(request: Request, exam: Exam) -> bool:
    s = request.app.state.settings
    return bool(exam.require_facereg and s.facereg_required)


def _expire_if_needed(db: Session, att: Attempt) -> None:
    if att.status == "in_progress" and att.deadline_at and time.time() > att.deadline_at + GRACE_SECONDS:
        _finalize(db, att)


def _finalize(db: Session, att: Attempt) -> None:
    rows = db.execute(select(Answer, Question.points).join(Question, Question.id == Answer.question_id)
                      .where(Answer.attempt_id == att.id)).all()
    att.score = round(sum(p for a, p in rows if a.is_correct), 3)
    att.status = "submitted"
    att.submitted_at = time.time()
    db.commit()


@router.get("/state")
def state(request: Request, att: Attempt = Depends(current_attempt), db: Session = Depends(get_db)):
    _expire_if_needed(db, att)
    exam, cand, _ = _ctx(db, att)
    fr = request.app.state.facereg
    total = len(exam.questions)
    answered_pos = [pos for (pos,) in db.execute(
        select(PaperItem.position).join(Answer, (Answer.attempt_id == PaperItem.attempt_id) & (Answer.question_id == PaperItem.question_id))
        .where(PaperItem.attempt_id == att.id, Answer.value_json.not_in(["null", '""', "[]"])))]
    return {
        "attempt": {"id": att.id, "status": att.status, "watermark": att.watermark, "pseudonym": att.pseudonym,
                    "started_at": att.started_at, "deadline_at": att.deadline_at, "server_time": time.time(),
                    "device_tier": att.device_tier, "facereg_status": att.facereg_status, "score": None},
        "exam": {"code": exam.code, "title": exam.title, "duration_minutes": exam.duration_minutes,
                 "per_question_seconds": exam.per_question_seconds, "allow_back": exam.allow_back,
                 "total_questions": total},
        "candidate": {"code": cand.code, "full_name": cand.full_name},
        "facereg": {"required": _facereg_required(request, exam), "enabled": fr.enabled,
                    "checkin_url": fr.checkin_url(cand.code, exam.code) if fr.enabled else None},
        "progress": {"answered": len(answered_pos), "answered_positions": sorted(answered_pos), "total": total},
        "reverify": {"min": request.app.state.settings.reverify_min_seconds,
                     "max": request.app.state.settings.reverify_max_seconds},
        "warnings": {"enabled": request.app.state.settings.candidate_warnings,
                     "cooldown": request.app.state.settings.warning_cooldown_seconds},
        "schema_version": request.app.state.settings.schema["version"],
    }


class DeviceIn(BaseModel):
    cores: int = 0
    memory_gb: float = 0
    face_ms: float = 0          # thời gian 1 lần chạy BlazeFace đo trên máy thí sinh
    user_agent: str = Field("", max_length=400)
    screen: dict = {}
    extended_screen: bool | None = None


def device_tier(d: DeviceIn) -> str:
    if (d.cores and d.cores <= 2) or (d.memory_gb and d.memory_gb <= 4) or d.face_ms > 40:
        return "low"
    if d.cores >= 8 and d.memory_gb >= 8 and 0 < d.face_ms < 15:
        return "high"
    return "mid"


@router.post("/device")
def device(body: DeviceIn, request: Request, att: Attempt = Depends(current_attempt), db: Session = Depends(get_db)):
    att.device_json = body.model_dump_json()
    att.device_tier = device_tier(body)
    db.commit()
    request.app.state.pipeline.forget(att.id)  # ngưỡng cờ theo hạng máy mới
    return {"tier": att.device_tier}


class FaceRegCallbackIn(BaseModel):
    ftoken: str = Field(..., min_length=8, max_length=128)


@router.post("/facereg/callback")
async def facereg_callback(body: FaceRegCallbackIn, request: Request, att: Attempt = Depends(current_attempt),
                           db: Session = Depends(get_db)):
    fr = request.app.state.facereg
    if not fr.enabled:
        raise HTTPException(409, "Chưa cấu hình FACEREG_URL.")
    exam, cand, _ = _ctx(db, att)
    try:
        info = await fr.resolve_token(body.ftoken)
    except FaceRegError as e:
        raise HTTPException(400, str(e)) from e
    if info["candidate_code"] != cand.code or (info["exam_code"] and info["exam_code"] != exam.code):
        raise HTTPException(403, "Kết quả check-in không thuộc thí sinh / kỳ thi này.")
    att.facereg_token = body.ftoken
    att.facereg_status = info["final_status"]
    if info["final_status"] == "ACCEPT" and att.status == "precheck":
        att.status = "verified"
    db.commit()
    return {"facereg_status": att.facereg_status, "status": att.status}


@router.post("/facereg/refresh")
async def facereg_refresh(request: Request, att: Attempt = Depends(current_attempt), db: Session = Depends(get_db)):
    """Chờ giám thị FaceReg duyệt ca REVIEW: kiểm tra lại trạng thái."""
    if not att.facereg_token:
        raise HTTPException(409, "Chưa check-in FaceReg.")
    return await facereg_callback(FaceRegCallbackIn(ftoken=att.facereg_token), request, att, db)


@router.post("/facereg/skip")
def facereg_skip(request: Request, att: Attempt = Depends(current_attempt), db: Session = Depends(get_db)):
    exam, _, _ = _ctx(db, att)
    if _facereg_required(request, exam):
        raise HTTPException(403, "Kỳ thi bắt buộc xác thực khuôn mặt.")
    if att.status == "precheck":
        att.status, att.facereg_status = "verified", "SKIPPED"
        db.commit()
    return {"status": att.status}


@router.post("/start")
def start(request: Request, att: Attempt = Depends(current_attempt), db: Session = Depends(get_db)):
    exam, _, _ = _ctx(db, att)
    if att.status == "in_progress":
        return {"status": att.status, "deadline_at": att.deadline_at}
    if att.status != "verified":
        raise HTTPException(409, "Cần hoàn tất kiểm tra thiết bị và xác thực khuôn mặt trước.")
    if exam.status != "open":
        raise HTTPException(403, "Kỳ thi chưa mở hoặc đã đóng.")
    s = request.app.state.settings
    if not db.scalar(select(func.count()).select_from(PaperItem).where(PaperItem.attempt_id == att.id)):
        qs = [db.get(Question, x.question_id) for x in exam.questions]
        db.add_all(generate_paper(s.secret_key, exam, att, [q for q in qs if q]))
    now = time.time()
    att.started_at = now
    att.deadline_at = now + exam.duration_minutes * 60
    att.status = "in_progress"
    att.review_sample = random.random() < s.review_sample_rate  # mẫu rà soát ngẫu nhiên (ước lượng recall)
    db.commit()
    return {"status": att.status, "deadline_at": att.deadline_at}


def _require_in_progress(db: Session, att: Attempt) -> None:
    _expire_if_needed(db, att)
    if att.status != "in_progress":
        raise HTTPException(409, "Bài thi không ở trạng thái đang làm.")


def _event(db: Session, att: Attempt, kind: str, detail: dict) -> None:
    db.add(SessionEvent(attempt_id=att.id, t=time.time() - (att.started_at or time.time()), kind=kind,
                        detail_json=json.dumps(detail)))


@router.get("/question/{pos}")
def get_question(pos: int, att: Attempt = Depends(current_attempt), db: Session = Depends(get_db)):
    _require_in_progress(db, att)
    exam, _, _ = _ctx(db, att)
    item = db.scalar(select(PaperItem).where(PaperItem.attempt_id == att.id, PaperItem.position == pos))
    if not item:
        raise HTTPException(404, "Không có câu này.")
    if not exam.allow_back:
        last = db.scalars(select(SessionEvent).where(SessionEvent.attempt_id == att.id, SessionEvent.kind == "q_view")
                          .order_by(SessionEvent.id.desc()).limit(1)).first()
        if last and json.loads(last.detail_json).get("pos", -1) > pos:
            raise HTTPException(403, "Kỳ thi không cho quay lại câu trước.")
    q = db.get(Question, item.question_id)
    r = render_item(item, q)
    ans = db.scalar(select(Answer).where(Answer.attempt_id == att.id, Answer.question_id == q.id))
    current = None
    if ans:
        v = json.loads(ans.value_json)
        if q.qtype == "single" and v:
            current = r.option_ids.index(v[0]) if v[0] in r.option_ids else None
        elif q.qtype == "multi":
            current = [r.option_ids.index(x) for x in v if x in r.option_ids]
        else:
            current = v
    _event(db, att, "q_view", {"pos": pos})
    db.commit()
    return {"position": pos, "total": len(exam.questions), "type": q.qtype, "stem": r.stem,
            "options": r.options, "answer": current, "expected_seconds": q.expected_seconds,
            "per_question_seconds": exam.per_question_seconds}


class AnswerIn(BaseModel):
    position: int
    value: int | list[int] | float | str | None = None
    client_seq: int = Field(0, ge=0)
    t: float = 0.0                      # giây kể từ khi bắt đầu (đồng hồ máy thí sinh)


@router.post("/answer")
def answer(body: AnswerIn, att: Attempt = Depends(current_attempt), db: Session = Depends(get_db)):
    _require_in_progress(db, att)
    item = db.scalar(select(PaperItem).where(PaperItem.attempt_id == att.id, PaperItem.position == body.position))
    if not item:
        raise HTTPException(404, "Không có câu này.")
    q = db.get(Question, item.question_id)
    try:
        original = to_original(item, q, body.value)
    except (ValueError, IndexError, TypeError) as e:
        raise HTTPException(422, "Câu trả lời không hợp lệ.") from e
    t = max(0.0, min(body.t, time.time() - (att.started_at or time.time()) + 5))
    ans = db.scalar(select(Answer).where(Answer.attempt_id == att.id, Answer.question_id == q.id))
    if ans and body.client_seq and body.client_seq <= ans.client_seq:
        return {"ok": True, "duplicate": True}
    correct = grade(q, item, original)
    if ans:
        if ans.value_json != json.dumps(original):
            ans.changes += 1
        ans.value_json, ans.is_correct, ans.last_answer_t, ans.client_seq = json.dumps(original), correct, t, body.client_seq
    else:
        db.add(Answer(attempt_id=att.id, question_id=q.id, value_json=json.dumps(original), is_correct=correct,
                      first_answer_t=t, last_answer_t=t, client_seq=body.client_seq))
    db.commit()
    return {"ok": True}


@router.post("/submit")
def submit(request: Request, att: Attempt = Depends(current_attempt), db: Session = Depends(get_db)):
    if att.status == "in_progress":
        _finalize(db, att)
        request.app.state.pipeline.forget(att.id)
    return {"status": att.status}


@router.post("/reverify")
async def reverify(request: Request, image: UploadFile = File(...), reason: str = Form("periodic"),
                   att: Attempt = Depends(current_attempt), db: Session = Depends(get_db)):
    """Ảnh CROP khuôn mặt -> FaceReg -> chỉ lưu điểm. Ảnh không được ghi xuống đĩa."""
    _require_in_progress(db, att)
    s = request.app.state.settings
    nxt = random.randint(s.reverify_min_seconds, s.reverify_max_seconds)
    fr = request.app.state.facereg
    if not fr.enabled or not att.facereg_token:
        return {"status": "SKIPPED", "next_in": nxt}
    data = await image.read(400_000)
    res = await fr.reverify(att.facereg_token, data)
    del data
    if res["status"] == "RATE_LIMITED":
        return {"status": "RATE_LIMITED", "next_in": 10}
    t = time.time() - (att.started_at or time.time())
    db.add(IdentityCheck(attempt_id=att.id, t=t, status=res["status"], score=res.get("score"),
                         reason="reappear" if reason == "reappear" else "periodic"))
    db.commit()
    request.app.state.pipeline.on_identity(att.id, res["status"], res.get("score"))
    return {"status": res["status"], "next_in": nxt}


class TelemetryIn(BaseModel):
    windows: list[dict] = Field(default_factory=list, max_length=1000)


@router.post("/telemetry")
async def telemetry(body: TelemetryIn, request: Request, att: Attempt = Depends(current_attempt)):
    """Đường dự phòng khi WebSocket không dùng được (và để gửi bù dữ liệu đệm trong IndexedDB)."""
    if att.status != "in_progress":
        raise HTTPException(409, "Bài thi không ở trạng thái đang làm.")
    n = await request.app.state.pipeline.ingest_windows(att.id, body.windows)
    return {"accepted": n, "ack": max((int(w.get("t", -1)) for w in body.windows), default=-1)}


# ------------------------------------------------------------------ WebSocket
ws_router = APIRouter()


@ws_router.websocket("/ws/exam")
async def ws_exam(ws: WebSocket, token: str = ""):
    app = ws.app
    try:
        claims = decode_token(app.state.settings.secret_key, token)
    except Exception:  # noqa: BLE001
        await ws.close(code=4401)
        return
    if claims.get("role") != "candidate":
        await ws.close(code=4403)
        return
    attempt_id = int(claims["sub"])
    with dbmod.SessionLocal() as db:
        att = db.get(Attempt, attempt_id)
        if not att or att.status != "in_progress":
            await ws.close(code=4409)
            return
    await ws.accept()
    pipe = app.state.pipeline
    pipe.candidates.subscribe(attempt_id, ws)  # nhận cảnh báo tức thời từ server
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "windows":
                windows = msg.get("windows") or []
                await pipe.ingest_windows(attempt_id, windows[:1000])
                await ws.send_json({"type": "ack", "t": max((int(w.get("t", -1)) for w in windows), default=-1)})
            elif kind == "frame":
                try:
                    jpeg = base64.b64decode(msg.get("jpeg", ""), validate=True)
                except (binascii.Error, ValueError):
                    continue
                ok = await pipe.ingest_frame(attempt_id, jpeg, float(msg.get("t", 0)), bool(msg.get("hi")),
                                             str(msg.get("why", ""))[:32])
                if msg.get("id") is not None:
                    await ws.send_json({"type": "frame_ack", "id": msg["id"], "ok": ok})
            elif kind == "ping":
                await ws.send_json({"type": "pong", "server_time": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        await ws.close(code=1011)
    finally:
        pipe.candidates.unsubscribe(attempt_id, ws)
