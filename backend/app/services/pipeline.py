"""Pipeline phía server: đặc trưng 2 giây + khung mờ mặt -> điểm rủi ro -> cờ -> dashboard giám thị.

  gateway (WebSocket/REST) --publish--> bus --worker đặc trưng--> chấm rủi ro, ghi lô xuống CSDL, cờ, đẩy dashboard
                                         \--worker khung------> YOLO theo lô -> cập nhật xác suất vật thể (khung bị bỏ)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sqlalchemy import insert, select, update

from .. import db as dbmod
from ..config import Settings
from ..models import Attempt, FeatureWindow, Flag, Registration, SessionEvent
from .bus import CandidateHub, Hub, InMemoryBus
from .risk import FeatureSpec, FusionScorer, RuleScorer, load_scorer, update_risk
from .vision import load_detector

log = logging.getLogger("examguard.pipeline")

OBJECT_GROUPS = ["obj_phone", "obj_book", "obj_headphone", "obj_person_extra", "obj_screen"]
OBJECT_TTL = 15.0      # kết quả vật thể còn hiệu lực bao lâu (giây)
# Luật chỉ server phát hiện được (trình duyệt không biết) -> đẩy cảnh báo tức thời cho thí sinh.
# Các hành vi trình duyệt tự phát hiện (rời tab, dán, mất mặt...) được cảnh báo ngay trên máy, không qua server.
SERVER_WARNINGS = ("PHONE", "PERSON_EXTRA", "HEADPHONE", "BOOK", "SCREEN")
WINDOW = 2.0


@dataclass
class AttemptState:
    attempt_id: int
    exam_id: int
    tier: str
    threshold: float
    risk: float = 0.0
    peak: float = 0.0
    h: np.ndarray | None = None
    id_status: str = ""
    id_score: float = 0.0
    id_new: bool = False
    objects: dict[str, tuple[float, float]] = field(default_factory=dict)  # nhóm -> (xác suất, thời điểm)
    last_frame_at: float = 0.0
    open_flag: int | None = None
    flag_reasons: Counter = field(default_factory=Counter)
    flag_peak: float = 0.0
    flag_evidence: list[str] = field(default_factory=list)
    last_push: float = -1.0
    last_push_at: float = 0.0
    burst: deque = field(default_factory=lambda: deque(maxlen=40))  # (thời điểm, t_rel, jpeg) khung chùm sự kiện
    last_t_index: int = -1
    warned: dict[str, float] = field(default_factory=dict)  # mã cảnh báo -> lần gửi gần nhất


class Pipeline:
    def __init__(self, settings: Settings, bus=None, hub: Hub | None = None):
        self.s = settings
        self.spec = FeatureSpec(settings.schema)
        self.scorer = load_scorer(settings.fusion_model, self.spec)
        self.detector = load_detector(settings.yolo_model, settings.yolo_classes)
        self.bus = bus or InMemoryBus(settings.frame_queue_max)
        self.hub = hub or Hub()
        self.candidates = CandidateHub()
        self.states: dict[int, AttemptState] = {}
        self.tasks: list[asyncio.Task] = []
        self.latencies: deque = deque(maxlen=5000)   # giây: nhận cửa sổ -> cập nhật dashboard
        self.frame_stats = Counter()

    # ------------------------------------------------------------------ vòng đời
    def start(self) -> None:
        self.tasks = [asyncio.create_task(self._feature_loop(), name="features"),
                      asyncio.create_task(self._frame_loop(), name="frames")]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.bus.close()

    # ------------------------------------------------------------------ đầu vào
    async def ingest_windows(self, attempt_id: int, windows: list[dict]) -> int:
        rx = time.time()
        n = 0
        for w in windows:
            if not isinstance(w, dict) or "t" not in w:
                continue
            await self.bus.publish_features({"a": attempt_id, "w": w, "rx": rx})
            n += 1
        return n

    async def ingest_frame(self, attempt_id: int, jpeg: bytes, t_rel: float, high: bool, reason: str = "") -> bool:
        if len(jpeg) > 300_000:
            return False
        return await self.bus.publish_frame({"a": attempt_id, "t": t_rel, "hi": high, "why": reason, "jpeg": jpeg,
                                             "rx": time.time()}, high)

    def on_identity(self, attempt_id: int, status: str, score: float | None) -> None:
        st = self.states.get(attempt_id)
        if st:
            st.id_status, st.id_score, st.id_new = status, float(score or 0.0), True

    # ------------------------------------------------------------------ trạng thái phiên
    def _load_state(self, attempt_id: int) -> AttemptState | None:
        with dbmod.SessionLocal() as db:
            row = db.execute(select(Attempt, Registration.exam_id).join(Registration, Registration.id == Attempt.registration_id)
                             .where(Attempt.id == attempt_id)).first()
            if not row:
                return None
            att, exam_id = row
            open_flag = db.scalar(select(Flag.id).where(Flag.attempt_id == attempt_id, Flag.status == "open",
                                                        Flag.end_t >= 0).order_by(Flag.id.desc()).limit(1))
        thr = self.s.tier_thresholds.get(att.device_tier, self.s.flag_threshold)
        st = AttemptState(attempt_id, exam_id, att.device_tier, thr, risk=att.risk, peak=att.risk_peak)
        st.open_flag = open_flag if att.risk >= 0.7 * thr else None
        if isinstance(self.scorer, FusionScorer):
            st.h = self.scorer.init_state()
        return st

    async def state(self, attempt_id: int) -> AttemptState | None:
        st = self.states.get(attempt_id)
        if st is None:
            st = await asyncio.to_thread(self._load_state, attempt_id)
            if st:
                self.states[attempt_id] = st
        return st

    def forget(self, attempt_id: int) -> None:
        self.states.pop(attempt_id, None)

    # ------------------------------------------------------------------ đặc trưng
    def _server_features(self, st: AttemptState, now: float) -> tuple[dict, dict]:
        v: dict[str, float] = {}
        m: dict[str, int] = {}
        m["identity"] = 1 if st.id_status else 0
        v["id_score"] = st.id_score
        v["id_checked"] = 1.0 if st.id_new else 0.0
        v["id_mismatch"] = 1.0 if st.id_status == "MISMATCH" else 0.0
        st.id_new = False
        fresh = self.detector is not None and now - st.last_frame_at <= 30
        m["object"] = 1 if fresh else 0
        for g in OBJECT_GROUPS:
            conf, t = st.objects.get(g, (0.0, 0.0))
            v[g] = conf if now - t <= OBJECT_TTL else 0.0
        v["obj_age"] = min(now - st.last_frame_at, 30.0) if st.last_frame_at else 30.0
        return v, m

    async def _feature_loop(self) -> None:
        while True:
            try:
                msgs = await self.bus.consume_features(500, 0.5)
                if msgs:
                    await self._process_features(msgs)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Lỗi worker đặc trưng")
                await asyncio.sleep(0.5)

    async def _process_features(self, msgs: list[dict]) -> None:
        now = time.time()
        rows, events, flag_ops, touched, warnings = [], [], [], {}, []
        msgs.sort(key=lambda x: (x["a"], x["w"]["t"]))
        for msg in msgs:
            st = await self.state(int(msg["a"]))
            if st is None:
                continue
            w = msg["w"]
            t_index = int(w["t"])
            values = {k: float(v) for k, v in (w.get("v") or {}).items() if k in self.spec.index and _finite(v)}
            masks = {k: int(bool(v)) for k, v in (w.get("m") or {}).items()}
            sv, sm = self._server_features(st, now)
            values.update(sv)
            masks.update(sm)
            masks.setdefault("answer", 1)
            if isinstance(self.scorer, FusionScorer):
                prob, reasons, st.h = self.scorer.score(values, masks, st.h)
            else:
                prob, reasons = self.scorer.score(values, masks)
            replay = t_index <= st.last_t_index  # cửa sổ gửi bù sau khi mất mạng: lưu nhưng không làm đổi rủi ro hiện tại
            if not replay:
                st.risk = update_risk(st.risk, prob)
                st.peak = max(st.peak, st.risk)
                st.last_t_index = t_index
            rows.append({"attempt_id": st.attempt_id, "t_index": t_index,
                         "values_json": json.dumps([round(values.get(n, 0.0), 4) for n in self.spec.names]),
                         "masks_json": json.dumps([masks.get(mo, 0) for mo in self.spec.modalities]),
                         "risk": st.risk, "window_score": round(prob, 4),
                         "reasons_json": json.dumps([r["code"] for r in reasons]), "created_at": now})
            for e in (w.get("ev") or [])[:50]:
                events.append({"attempt_id": st.attempt_id, "t": float(e.get("t", t_index * WINDOW)),
                               "kind": str(e.get("k", "event"))[:32], "detail_json": json.dumps(e.get("d", {}))[:2000]})
            if not replay:
                ops = self._flag_logic(st, t_index * WINDOW, reasons, now)
                flag_ops.extend(ops)
                warnings.extend(self._server_warnings(st, reasons, ops, now))
            touched[st.attempt_id] = (st, msg.get("rx", now))
        await asyncio.to_thread(self._flush, rows, events, flag_ops, [st for st, _ in touched.values()], now)
        for attempt_id, msg in warnings:
            await self.candidates.send(attempt_id, msg)
        for st, rx in touched.values():
            changed = abs(st.risk - st.last_push) >= 2 or now - st.last_push_at > 10
            if changed or any(op["st"] is st for op in flag_ops):
                st.last_push, st.last_push_at = st.risk, now
                await self.hub.publish(st.exam_id, {"type": "attempt", "id": st.attempt_id, "risk": st.risk,
                                                    "peak": st.peak, "flagged": st.open_flag is not None,
                                                    "last_seen": now})
                self.latencies.append(time.time() - rx)

    def _server_warnings(self, st: AttemptState, reasons: list[dict], flag_ops: list[dict], now: float) -> list[tuple[int, dict]]:
        if not self.s.candidate_warnings:
            return []
        codes = [r["code"] for r in reasons if r["code"] in SERVER_WARNINGS]
        if self.s.warn_on_flag and any(op["op"] == "create" for op in flag_ops):
            codes.append("FLAGGED")
        out = []
        for code in codes:
            if now - st.warned.get(code, 0) >= self.s.warning_cooldown_seconds:
                st.warned[code] = now
                out.append((st.attempt_id, {"type": "warning", "code": code}))
        return out

    def _flag_logic(self, st: AttemptState, t_rel: float, reasons: list[dict], now: float) -> list[dict]:
        ops = []
        if st.risk >= st.threshold:
            for r in reasons:
                st.flag_reasons[r["label"]] += 1
            st.flag_peak = max(st.flag_peak, st.risk)
            if st.open_flag is None:
                st.open_flag = -1  # sẽ có id sau khi ghi
                ops.append({"op": "create", "st": st, "t": t_rel})
            else:
                ops.append({"op": "update", "st": st, "t": t_rel})
        elif st.open_flag is not None and st.risk < 0.7 * st.threshold:
            ops.append({"op": "close", "st": st, "t": t_rel})
        return ops

    def _save_evidence(self, st: AttemptState, flag_id: int) -> list[str]:
        if self.s.evidence_frames != "flagged" or not st.burst:
            return []
        folder = self.s.evidence_dir / f"attempt_{st.attempt_id}"
        folder.mkdir(parents=True, exist_ok=True)
        names = []
        cutoff = time.time() - 20
        for at, t_rel, jpeg in list(st.burst):
            if at < cutoff:
                continue
            name = f"flag{flag_id}_{t_rel:.0f}_{int(at * 1000) % 100000}.jpg"
            if name in st.flag_evidence:
                continue
            (folder / name).write_bytes(jpeg)
            names.append(name)
        st.burst.clear()
        return names[-12:]

    def _flush(self, rows, events, flag_ops, states, now) -> None:
        with dbmod.SessionLocal() as db:
            if rows:
                db.execute(_insert_ignore(db, FeatureWindow), rows)
            if events:
                db.execute(insert(SessionEvent), events)
            for op in flag_ops:
                st = op["st"]
                if op["op"] == "create":
                    f = Flag(attempt_id=st.attempt_id, start_t=max(op["t"] - 6, 0), end_t=op["t"], score=st.risk,
                             reasons_json=json.dumps(st.flag_reasons.most_common(8), ensure_ascii=False))
                    db.add(f)
                    db.flush()
                    st.open_flag = f.id
                    st.flag_evidence = self._save_evidence(st, f.id)
                    f.evidence_json = json.dumps(st.flag_evidence)
                elif st.open_flag and st.open_flag > 0:
                    f = db.get(Flag, st.open_flag)
                    if f:
                        f.end_t = op["t"]
                        f.score = max(f.score, st.flag_peak)
                        f.reasons_json = json.dumps(st.flag_reasons.most_common(8), ensure_ascii=False)
                        new = self._save_evidence(st, f.id)
                        if new:
                            st.flag_evidence = (st.flag_evidence + new)[-24:]
                            f.evidence_json = json.dumps(st.flag_evidence)
                    if op["op"] == "close":
                        st.open_flag = None
                        st.flag_reasons = Counter()
                        st.flag_peak = 0.0
                        st.flag_evidence = []
            for st in states:
                db.execute(update(Attempt).where(Attempt.id == st.attempt_id).values(
                    risk=st.risk, risk_peak=st.peak, last_seen_at=now,
                    **({"flagged": True} if st.open_flag is not None else {})))
            db.commit()

    # ------------------------------------------------------------------ khung hình
    async def _frame_loop(self) -> None:
        while True:
            try:
                frames = await self.bus.consume_frames(self.s.yolo_batch, 0.5)
                if frames:
                    await self._process_frames(frames)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Lỗi worker khung hình")
                await asyncio.sleep(0.5)

    async def _process_frames(self, frames: list[dict]) -> None:
        now = time.time()
        results = await asyncio.to_thread(self.detector.predict, [f["jpeg"] for f in frames]) if self.detector else [None] * len(frames)
        for f, res in zip(frames, results):
            st = await self.state(int(f["a"]))
            if st is None:
                continue
            self.frame_stats["hi" if f.get("hi") else "lo"] += 1
            st.last_frame_at = now
            if res:
                for g, conf in res.items():
                    st.objects[g] = (conf, now)
            if f.get("hi") and self.s.evidence_frames == "flagged":
                st.burst.append((now, float(f.get("t", 0)), f["jpeg"]))  # chỉ giữ tạm trong RAM, ghi khi có cờ
            # khung không được lưu xuống đĩa ở bước này

    # ------------------------------------------------------------------ thống kê
    def stats(self) -> dict:
        lat = sorted(self.latencies)

        def pct(p):
            return round(lat[min(int(p * len(lat)), len(lat) - 1)], 3) if lat else None

        return {"bus": self.bus.stats(), "scorer": self.scorer.name, "yolo": self.detector is not None,
                "active_attempts": len(self.states), "frames_processed": dict(self.frame_stats),
                "latency_p50": pct(0.5), "latency_p95": pct(0.95), "latency_p99": pct(0.99)}


def _finite(v) -> bool:
    try:
        return np.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _insert_ignore(db, model):
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(model).on_conflict_do_nothing()
    return insert(model).prefix_with("OR IGNORE")


def evidence_path(settings: Settings, attempt_id: int, name: str) -> Path:
    if "/" in name or "\\" in name or not name.endswith(".jpg"):
        raise ValueError("bad name")
    return settings.evidence_dir / f"attempt_{attempt_id}" / name
