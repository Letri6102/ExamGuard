"""Kiểm thử tải: giả lập N thí sinh gửi đặc trưng 2 giây + khung hình qua WebSocket (Giai đoạn 9, C3).

    python loadtest/simulate_candidates.py --url http://localhost:8100 --exam LOAD01 -n 500 --minutes 3

Tự tạo kỳ thi + N thí sinh qua API quản trị (cần ADMIN_USERNAME/ADMIN_PASSWORD), đăng nhập, bắt đầu làm bài, rồi mỗi
thí sinh mở MỘT WebSocket gửi 1 vector / 2 giây và 1 khung 320px / 5–10 giây (5% thí sinh có hành vi gian lận để sinh cờ).
In ra: tin nhắn/giây, độ trễ ack p50/p95/p99, lỗi, và thống kê pipeline phía server (độ trễ hành vi -> dashboard).
Chỉ chạy trên server thử nghiệm của bạn.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import random
import statistics
import sys
import time

import httpx

try:
    import websockets
except ImportError:
    sys.exit("Cần: pip install websockets httpx")


def jpeg_320() -> str:
    try:
        import numpy as np
        import cv2
        img = (np.random.default_rng(0).random((240, 320, 3)) * 255).astype("uint8")
        return base64.b64encode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()).decode()
    except ImportError:
        return base64.b64encode(b"\xff\xd8" + os.urandom(12000) + b"\xff\xd9").decode()


async def setup(url: str, exam: str, n: int, admin: tuple[str, str]) -> list[tuple[str, str]]:
    async with httpx.AsyncClient(base_url=url, timeout=60) as c:
        r = await c.post("/api/auth/login", json={"username": admin[0], "password": admin[1]})
        r.raise_for_status()
        H = {"Authorization": "Bearer " + r.json()["token"]}
        exams = (await c.get("/api/admin/exams", headers=H)).json()
        ex = next((e for e in exams if e["code"] == exam), None)
        if not ex:
            q = await c.post("/api/admin/questions", headers=H, json={"type": "single", "stem": "Câu thử tải?", "options": ["A", "B"], "answer": ["A"]})
            ex = (await c.post("/api/admin/exams", headers=H, json={"code": exam, "title": "Thử tải", "duration_minutes": 120,
                                                                    "require_facereg": False})).json()
            await c.put(f"/api/admin/exams/{ex['id']}/questions", headers=H, json={"question_ids": [q.json()["id"]]})
        csv = "code,full_name\n" + "\n".join(f"LT{i:05d},Thi sinh {i}" for i in range(n))
        await c.post(f"/api/admin/exams/{ex['id']}/candidates/import", headers=H, files={"file": ("ds.csv", csv.encode())})
        await c.post(f"/api/admin/exams/{ex['id']}/status", headers=H, json={"status": "open"})
        roster = {r["code"]: r["access_code"] for r in (await c.get(f"/api/admin/exams/{ex['id']}/candidates", headers=H)).json()}
        tokens = []
        sem = asyncio.Semaphore(50)

        async def login(i):
            async with sem:
                code = f"LT{i:05d}"
                r = await c.post("/api/auth/candidate-login", json={"exam_code": exam, "candidate_code": code, "access_code": roster[code]})
                tok = r.json()["token"]
                h = {"Authorization": "Bearer " + tok}
                await c.post("/api/exam/facereg/skip", headers=h)
                await c.post("/api/exam/start", headers=h)
                tokens.append((code, tok))
        await asyncio.gather(*(login(i) for i in range(n)))
        return tokens


async def candidate(url: str, token: str, seconds: float, cheat: bool, frame: str, stats: dict):
    ws_url = url.replace("http", "ws", 1) + f"/ws/exam?token={token}"
    t_end = time.time() + seconds
    try:
        async with websockets.connect(ws_url, max_size=2 ** 22) as ws:
            t, next_frame = 0, time.time() + random.uniform(0, 8)
            await asyncio.sleep(random.uniform(0, 2))
            while time.time() < t_end:
                v = {"face_count_mean": 1, "face_count_max": 1, "face_conf": 0.9, "keys": random.randint(0, 12)}
                if cheat and 30 <= t <= 40:
                    v.update(hidden_sec=2, tab_switches=1 if t == 30 else 0, chars_after_return=40)
                sent = time.perf_counter()
                await ws.send(json.dumps({"type": "windows", "windows": [{"t": t, "v": v, "m": {"face": 1, "browser": 1, "keystroke": 1, "answer": 1, "system": 1}, "ev": []}]}))
                msg = json.loads(await ws.recv())
                stats["ack"].append(time.perf_counter() - sent)
                stats["msgs"] += 1
                if time.time() >= next_frame:
                    await ws.send(json.dumps({"type": "frame", "hi": False, "t": t * 2, "jpeg": frame}))
                    stats["frames"] += 1
                    next_frame = time.time() + random.uniform(5, 10)
                t += 1
                await asyncio.sleep(max(0, 2 - (time.perf_counter() - sent)))
    except Exception as e:  # noqa: BLE001
        stats["errors"].append(type(e).__name__)


def pct(a, p):
    a = sorted(a)
    return a[min(int(p * len(a)), len(a) - 1)] * 1000 if a else float("nan")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8100")
    ap.add_argument("--exam", default="LOAD01")
    ap.add_argument("-n", type=int, default=200)
    ap.add_argument("--minutes", type=float, default=2)
    ap.add_argument("--admin-user", default=os.environ.get("ADMIN_USERNAME", "admin"))
    ap.add_argument("--admin-password", default=os.environ.get("ADMIN_PASSWORD", ""))
    a = ap.parse_args()
    if not a.admin_password:
        sys.exit("Đặt ADMIN_PASSWORD (hoặc --admin-password)")
    t0 = time.time()
    tokens = await setup(a.url, a.exam, a.n, (a.admin_user, a.admin_password))
    print(f"Đã chuẩn bị {len(tokens)} thí sinh trong {time.time() - t0:.0f}s; chạy {a.minutes} phút…")
    stats = {"ack": [], "msgs": 0, "frames": 0, "errors": []}
    frame = jpeg_320()
    t1 = time.time()
    await asyncio.gather(*(candidate(a.url, tok, a.minutes * 60, i < len(tokens) * 0.05, frame, stats)
                           for i, (_, tok) in enumerate(tokens)))
    dur = time.time() - t1
    print(f"\nThí sinh: {len(tokens)} | thời gian {dur:.0f}s | cửa sổ đặc trưng: {stats['msgs']} ({stats['msgs'] / dur:.0f}/s) | "
          f"khung: {stats['frames']} ({stats['frames'] / dur:.1f}/s)")
    print(f"Độ trễ ack (ms): p50 {pct(stats['ack'], .5):.1f} | p95 {pct(stats['ack'], .95):.1f} | p99 {pct(stats['ack'], .99):.1f}")
    print(f"Lỗi kết nối: {len(stats['errors'])} {dict((e, stats['errors'].count(e)) for e in set(stats['errors']))}")
    async with httpx.AsyncClient(base_url=a.url) as c:
        r = await c.post("/api/auth/login", json={"username": a.admin_user, "password": a.admin_password})
        s = (await c.get("/api/analysis/system", headers={"Authorization": "Bearer " + r.json()["token"]})).json()
    print("Pipeline server:", json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
