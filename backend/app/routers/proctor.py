"""Giám thị: theo dõi rủi ro, rà soát cờ (= nhãn), mẫu ngẫu nhiên, bằng chứng (có nhật ký), số liệu đánh giá."""
from __future__ import annotations

import csv
import io
import json
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Attempt, Candidate, Exam, FeatureWindow, Flag, IdentityCheck, Registration, SessionEvent, SessionReview, User,
)
from ..security import audit, decode_token, require_staff
from ..services.pipeline import evidence_path

router = APIRouter(prefix="/api/proctor", tags=["proctor"])


@router.get("/exams")
def exams(_: User = Depends(require_staff), db: Session = Depends(get_db)):
    out = []
    for e in db.scalars(select(Exam).where(Exam.status != "draft").order_by(Exam.id.desc())):
        base = select(func.count()).select_from(Attempt).join(Registration).where(Registration.exam_id == e.id)
        out.append({"id": e.id, "code": e.code, "title": e.title, "status": e.status,
                    "attempts": db.scalar(base), "in_progress": db.scalar(base.where(Attempt.status == "in_progress")),
                    "flagged": db.scalar(base.where(Attempt.flagged.is_(True)))})
    return out


def _attempt_rows(db: Session, exam_id: int):
    return db.execute(select(Attempt, Candidate).join(Registration, Registration.id == Attempt.registration_id)
                      .join(Candidate, Candidate.id == Registration.candidate_id)
                      .where(Registration.exam_id == exam_id)).all()


@router.get("/exams/{exam_id}/attempts")
def attempts(exam_id: int, _: User = Depends(require_staff), db: Session = Depends(get_db)):
    open_flags = dict(db.execute(select(Flag.attempt_id, func.count()).where(Flag.status == "open")
                                 .group_by(Flag.attempt_id)).all())
    reviewed = {a for (a,) in db.execute(select(SessionReview.attempt_id).distinct())}
    rows = []
    for a, c in _attempt_rows(db, exam_id):
        rows.append({"id": a.id, "pseudonym": a.pseudonym, "candidate_code": c.code, "full_name": c.full_name,
                     "status": a.status, "risk": a.risk, "peak": a.risk_peak, "flagged": a.flagged,
                     "open_flags": open_flags.get(a.id, 0), "last_seen": a.last_seen_at, "tier": a.device_tier,
                     "facereg_status": a.facereg_status, "review_sample": a.review_sample, "reviewed": a.id in reviewed,
                     "score": a.score})
    rows.sort(key=lambda r: (-r["open_flags"], -r["risk"]))
    return rows


@router.get("/attempts/{attempt_id}")
def attempt_detail(attempt_id: int, request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    a = db.get(Attempt, attempt_id)
    if not a:
        raise HTTPException(404, "Không tìm thấy phiên thi.")
    reg = db.get(Registration, a.registration_id)
    cand = db.get(Candidate, reg.candidate_id)
    spec = request.app.state.pipeline.spec
    wins = db.scalars(select(FeatureWindow).where(FeatureWindow.attempt_id == a.id).order_by(FeatureWindow.t_index)).all()
    timeline = [{"t": w.t_index * 2, "risk": w.risk, "s": w.window_score, "r": json.loads(w.reasons_json)} for w in wins]
    mods = spec.modalities
    avail = [0] * len(mods)
    for w in wins:
        for i, m in enumerate(json.loads(w.masks_json)):
            avail[i] += m
    flags = db.scalars(select(Flag).where(Flag.attempt_id == a.id).order_by(Flag.start_t)).all()
    events = db.scalars(select(SessionEvent).where(SessionEvent.attempt_id == a.id).order_by(SessionEvent.t).limit(2000)).all()
    ids = db.scalars(select(IdentityCheck).where(IdentityCheck.attempt_id == a.id).order_by(IdentityCheck.t)).all()
    reviews = db.execute(select(SessionReview, User.username).outerjoin(User, User.id == SessionReview.reviewer_id)
                         .where(SessionReview.attempt_id == a.id)).all()
    audit(db, request, user, "view_attempt", a.pseudonym)
    return {
        "attempt": {"id": a.id, "pseudonym": a.pseudonym, "status": a.status, "risk": a.risk, "peak": a.risk_peak,
                    "tier": a.device_tier, "device": json.loads(a.device_json or "{}"), "facereg_status": a.facereg_status,
                    "started_at": a.started_at, "submitted_at": a.submitted_at, "review_sample": a.review_sample,
                    "watermark": a.watermark, "score": a.score},
        "candidate": {"code": cand.code, "full_name": cand.full_name},
        "timeline": timeline,
        "modality_coverage": {m: round(avail[i] / len(wins), 3) if wins else 0 for i, m in enumerate(mods)},
        "flags": [{"id": f.id, "start_t": f.start_t, "end_t": f.end_t, "score": f.score, "status": f.status,
                   "reasons": json.loads(f.reasons_json), "evidence": json.loads(f.evidence_json), "note": f.note}
                  for f in flags],
        "events": [{"t": e.t, "kind": e.kind, "detail": json.loads(e.detail_json)} for e in events],
        "identity": [{"t": i.t, "status": i.status, "score": i.score, "reason": i.reason} for i in ids],
        "reviews": [{"verdict": r.verdict, "note": r.note, "by": u, "at": r.created_at} for r, u in reviews],
        "feature_names": spec.names, "modalities": mods,
    }


@router.get("/attempts/{attempt_id}/windows")
def attempt_windows(attempt_id: int, request: Request, start: float = 0, end: float = 1e9,
                    _: User = Depends(require_staff), db: Session = Depends(get_db)):
    wins = db.scalars(select(FeatureWindow).where(FeatureWindow.attempt_id == attempt_id,
                                                  FeatureWindow.t_index >= int(start // 2),
                                                  FeatureWindow.t_index <= int(end // 2))
                      .order_by(FeatureWindow.t_index).limit(1800)).all()
    return {"names": request.app.state.pipeline.spec.names,
            "rows": [{"t": w.t_index * 2, "v": json.loads(w.values_json), "m": json.loads(w.masks_json),
                      "s": w.window_score} for w in wins]}


class WarnIn(BaseModel):
    message: str = Field(..., min_length=3, max_length=300)


@router.post("/attempts/{attempt_id}/warn")
async def warn_candidate(attempt_id: int, body: WarnIn, request: Request, user: User = Depends(require_staff),
                         db: Session = Depends(get_db)):
    """Giám thị gửi cảnh báo tới màn hình thí sinh ngay lập tức (qua WebSocket đang mở)."""
    a = db.get(Attempt, attempt_id)
    if not a:
        raise HTTPException(404, "Không tìm thấy phiên thi.")
    if a.status != "in_progress":
        raise HTTPException(409, "Thí sinh không còn đang làm bài.")
    msg = body.message.strip()
    delivered = await request.app.state.pipeline.candidates.send(attempt_id, {"type": "warning", "code": "PROCTOR", "text": msg})
    t = time.time() - (a.started_at or time.time())
    db.add(SessionEvent(attempt_id=attempt_id, t=t, kind="proctor_warning",
                        detail_json=json.dumps({"text": msg, "by": user.username, "delivered": delivered}, ensure_ascii=False)))
    db.commit()
    audit(db, request, user, "warn_candidate", a.pseudonym, msg)
    return {"delivered": delivered}


class FlagReviewIn(BaseModel):
    status: str = Field(..., pattern="^(confirmed|dismissed|open)$")
    note: str = Field("", max_length=1000)


@router.post("/flags/{flag_id}/review")
def review_flag(flag_id: int, body: FlagReviewIn, request: Request, user: User = Depends(require_staff),
                db: Session = Depends(get_db)):
    f = db.get(Flag, flag_id)
    if not f:
        raise HTTPException(404, "Không tìm thấy cờ.")
    f.status, f.note, f.reviewer_id, f.reviewed_at = body.status, body.note, user.id, time.time()
    db.commit()
    audit(db, request, user, "review_flag", str(flag_id), body.status)
    return {"ok": True}


@router.get("/flags/{flag_id}/evidence/{name}")
def evidence(flag_id: int, name: str, request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    f = db.get(Flag, flag_id)
    if not f or name not in json.loads(f.evidence_json):
        raise HTTPException(404, "Không có bằng chứng.")
    try:
        p = evidence_path(request.app.state.settings, f.attempt_id, name)
    except ValueError as e:
        raise HTTPException(400, "Tên file không hợp lệ.") from e
    if not p.exists():
        raise HTTPException(410, "Bằng chứng đã bị xóa theo thời hạn lưu.")
    audit(db, request, user, "view_evidence", f"flag:{flag_id}", name)  # mọi lần xem bằng chứng đều được ghi
    return Response(p.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/exams/{exam_id}/review-queue")
def review_queue(exam_id: int, _: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Cờ đang mở + mẫu ngẫu nhiên các phiên KHÔNG bị gắn cờ (để ước lượng recall không thiên lệch)."""
    reviewed = {a for (a,) in db.execute(select(SessionReview.attempt_id).distinct())}
    flags, sample = [], []
    for a, c in _attempt_rows(db, exam_id):
        for f in db.scalars(select(Flag).where(Flag.attempt_id == a.id, Flag.status == "open")):
            flags.append({"flag_id": f.id, "attempt_id": a.id, "pseudonym": a.pseudonym, "candidate_code": c.code,
                          "start_t": f.start_t, "end_t": f.end_t, "score": f.score, "reasons": json.loads(f.reasons_json)})
        if a.review_sample and not a.flagged and a.id not in reviewed and a.status == "submitted":
            sample.append({"attempt_id": a.id, "pseudonym": a.pseudonym, "candidate_code": c.code, "peak": a.risk_peak})
    flags.sort(key=lambda x: -x["score"])
    return {"flags": flags, "sample": sample}


class SessionReviewIn(BaseModel):
    verdict: str = Field(..., pattern="^(clean|cheating|unsure)$")
    note: str = Field("", max_length=1000)


@router.post("/attempts/{attempt_id}/session-review")
def session_review(attempt_id: int, body: SessionReviewIn, request: Request, user: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    if not db.get(Attempt, attempt_id):
        raise HTTPException(404, "Không tìm thấy phiên thi.")
    db.add(SessionReview(attempt_id=attempt_id, reviewer_id=user.id, verdict=body.verdict, note=body.note))
    db.commit()
    audit(db, request, user, "session_review", str(attempt_id), body.verdict)
    return {"ok": True}


@router.get("/exams/{exam_id}/export.csv")
def export_windows(exam_id: int, request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Đặc trưng + nhãn giám thị để huấn luyện lại (notebooks/03). Chỉ có mã giả danh, không có danh tính."""
    spec = request.app.state.pipeline.spec
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["session", "t", *spec.names, *[f"mask_{m}" for m in spec.modalities], "window_label", "session_label", "tier"])
    for a, _c in _attempt_rows(db, exam_id):
        flags = db.scalars(select(Flag).where(Flag.attempt_id == a.id, Flag.status.in_(("confirmed", "dismissed")))).all()
        verdicts = [v for (v,) in db.execute(select(SessionReview.verdict).where(SessionReview.attempt_id == a.id))]
        confirmed = any(f.status == "confirmed" for f in flags) or "cheating" in verdicts
        clean = (not confirmed) and ("clean" in verdicts or (flags and all(f.status == "dismissed" for f in flags)))
        session_label = 1 if confirmed else 0 if clean else ""
        for fw in db.scalars(select(FeatureWindow).where(FeatureWindow.attempt_id == a.id).order_by(FeatureWindow.t_index)):
            t = fw.t_index * 2
            label = ""
            for f in flags:
                if f.start_t - 2 <= t <= f.end_t + 2:
                    label = 1 if f.status == "confirmed" else 0
            if label == "" and session_label == 0:
                label = 0
            w.writerow([a.pseudonym, t, *json.loads(fw.values_json), *json.loads(fw.masks_json), label, session_label,
                        a.device_tier])
    exam = db.get(Exam, exam_id)
    audit(db, request, user, "export_features", exam.code if exam else str(exam_id))
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="features_exam{exam_id}.csv"'})


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    if len(pairs) < 2:
        return None
    labels = sorted({x for p in pairs for x in p})
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pe = sum((sum(a == k for a, _ in pairs) / n) * (sum(b == k for _, b in pairs) / n) for k in labels)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


@router.get("/exams/{exam_id}/metrics")
def metrics(exam_id: int, _: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Số liệu tổng hợp cho triển khai thực tế (C4) và công bằng theo hạng máy (C5)."""
    rows = _attempt_rows(db, exam_id)
    ids = [a.id for a, _ in rows]
    flags = db.scalars(select(Flag).where(Flag.attempt_id.in_(ids))).all() if ids else []
    by_status = defaultdict(int)
    for f in flags:
        by_status[f.status] += 1
    reviewed = by_status["confirmed"] + by_status["dismissed"]
    tiers: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "flagged": 0, "dismissed_flags": 0, "confirmed_flags": 0})
    tier_of = {a.id: a.device_tier for a, _ in rows}
    for a, _ in rows:
        tiers[a.device_tier]["attempts"] += 1
        tiers[a.device_tier]["flagged"] += int(a.flagged)
    for f in flags:
        if f.status in ("confirmed", "dismissed"):
            tiers[tier_of[f.attempt_id]][f"{f.status}_flags"] += 1
    for t in tiers.values():
        t["flag_rate"] = round(t["flagged"] / t["attempts"], 3) if t["attempts"] else None
        r = t["confirmed_flags"] + t["dismissed_flags"]
        t["false_flag_share"] = round(t["dismissed_flags"] / r, 3) if r else None
    # mẫu ngẫu nhiên phiên KHÔNG bị gắn cờ -> tỉ lệ gian lận bị bỏ sót
    reviews = db.execute(select(SessionReview.attempt_id, SessionReview.verdict, SessionReview.reviewer_id)
                         .where(SessionReview.attempt_id.in_(ids))).all() if ids else []
    sampled = {a.id for a, _ in rows if a.review_sample and not a.flagged}
    sample_verdicts = [v for aid, v, _ in reviews if aid in sampled]
    miss_rate = round(sample_verdicts.count("cheating") / len(sample_verdicts), 3) if sample_verdicts else None
    per_attempt = defaultdict(list)
    for aid, v, rid in reviews:
        per_attempt[aid].append((rid, v))
    kappa_pairs = [(lst[0][1], lst[1][1]) for lst in per_attempt.values() if len({r for r, _ in lst}) >= 2]
    return {
        "attempts": len(rows), "flagged_attempts": sum(a.flagged for a, _ in rows), "flags": len(flags),
        "flags_by_status": dict(by_status),
        "flag_precision": round(by_status["confirmed"] / reviewed, 3) if reviewed else None,
        "unflagged_sample_size": len(sample_verdicts), "unflagged_cheating_rate": miss_rate,
        "reviewer_kappa": cohen_kappa(kappa_pairs), "kappa_pairs": len(kappa_pairs),
        "by_tier": dict(tiers),
    }


# ------------------------------------------------------------------ WebSocket dashboard
ws_router = APIRouter()


@ws_router.websocket("/ws/proctor")
async def ws_proctor(ws: WebSocket, token: str = "", exam_id: int = 0):
    try:
        claims = decode_token(ws.app.state.settings.secret_key, token)
    except Exception:  # noqa: BLE001
        await ws.close(code=4401)
        return
    if claims.get("role") not in ("admin", "proctor"):
        await ws.close(code=4403)
        return
    await ws.accept()
    hub = ws.app.state.pipeline.hub
    hub.subscribe(exam_id, ws)
    try:
        while True:
            await ws.receive_text()  # giữ kết nối; dashboard chỉ nhận
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(exam_id, ws)
