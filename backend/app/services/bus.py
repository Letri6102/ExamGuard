"""Hàng đợi giữa gateway (WebSocket) và các worker.

Luồng:
  features   : vector đặc trưng mỗi 2 giây (không bao giờ bỏ)
  frames:hi  : chùm khung theo sự kiện (mất mặt, 2 khuôn mặt, quay lại tab...) -> LUÔN giữ
  frames:lo  : khung mẫu định kỳ -> khi quá tải bỏ NGẪU NHIÊN (ước lượng được recall khi bỏ X% khung)

InMemoryBus: một tiến trình (phát triển / ca thi nhỏ).
RedisBus   : Redis Streams + consumer group, chạy nhiều gateway/worker (đặt REDIS_URL).
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from collections import deque


class InMemoryBus:
    kind = "memory"

    def __init__(self, frame_queue_max: int = 2000):
        self.features: asyncio.Queue[dict] = asyncio.Queue()
        self.hi: deque[dict] = deque()
        self.lo: deque[dict] = deque()
        self.max_lo = frame_queue_max
        self.frame_event = asyncio.Event()
        self.dropped = 0
        self.received = {"features": 0, "hi": 0, "lo": 0}
        self._rng = random.Random(0)

    async def publish_features(self, msg: dict) -> None:
        self.received["features"] += 1
        await self.features.put(msg)

    async def publish_frame(self, msg: dict, high: bool) -> bool:
        self.received["hi" if high else "lo"] += 1
        if high:
            self.hi.append(msg)
        else:
            if len(self.lo) >= self.max_lo:
                del self.lo[self._rng.randrange(len(self.lo))]  # bỏ ngẫu nhiên một khung mẫu
                self.dropped += 1
            self.lo.append(msg)
        self.frame_event.set()
        return True

    async def consume_features(self, max_n: int = 500, timeout: float = 0.5) -> list[dict]:
        out = []
        try:
            out.append(await asyncio.wait_for(self.features.get(), timeout))
        except asyncio.TimeoutError:
            return out
        while len(out) < max_n and not self.features.empty():
            out.append(self.features.get_nowait())
        return out

    async def consume_frames(self, max_n: int = 32, timeout: float = 0.5) -> list[dict]:
        if not self.hi and not self.lo:
            self.frame_event.clear()
            try:
                await asyncio.wait_for(self.frame_event.wait(), timeout)
            except asyncio.TimeoutError:
                return []
        out = []
        while self.hi and len(out) < max_n:
            out.append(self.hi.popleft())
        while self.lo and len(out) < max_n:
            out.append(self.lo.popleft())
        return out

    def stats(self) -> dict:
        return {"kind": self.kind, "features_queue": self.features.qsize(), "frames_hi": len(self.hi),
                "frames_lo": len(self.lo), "frames_dropped": self.dropped, "received": dict(self.received)}

    async def close(self) -> None:
        pass


class RedisBus:
    kind = "redis"
    GROUP = "workers"

    def __init__(self, url: str, frame_queue_max: int = 2000, client=None, consumer: str | None = None):
        if client is None:
            import redis.asyncio as aioredis

            client = aioredis.from_url(url)
        self.r = client
        self.max_lo = frame_queue_max
        self.consumer = consumer or f"c{random.randrange(1 << 30)}"
        self._ready = False
        self.dropped = 0

    async def _ensure(self) -> None:
        if self._ready:
            return
        for s in ("eg:features", "eg:frames:hi", "eg:frames:lo"):
            try:
                await self.r.xgroup_create(s, self.GROUP, id="0", mkstream=True)
            except Exception as e:  # noqa: BLE001  (nhóm đã tồn tại)
                if "BUSYGROUP" not in str(e):
                    raise
        self._ready = True

    async def publish_features(self, msg: dict) -> None:
        await self._ensure()
        await self.r.xadd("eg:features", {"m": json.dumps(msg)})

    async def publish_frame(self, msg: dict, high: bool) -> bool:
        await self._ensure()
        data = dict(msg)
        jpeg = data.pop("jpeg")
        fields = {"m": json.dumps(data), "jpeg": jpeg}
        if high:
            await self.r.xadd("eg:frames:hi", fields)
        else:  # cắt bớt phần cũ khi quá dài (Redis cắt theo thứ tự thời gian, không ngẫu nhiên)
            await self.r.xadd("eg:frames:lo", fields, maxlen=self.max_lo, approximate=True)
        return True

    async def _read(self, stream: str, n: int, block_ms: int) -> list[tuple[bytes, dict]]:
        res = await self.r.xreadgroup(self.GROUP, self.consumer, {stream: ">"}, count=n, block=block_ms)
        items = res[0][1] if res else []
        if items:
            await self.r.xack(stream, self.GROUP, *[i for i, _ in items])
            await self.r.xdel(stream, *[i for i, _ in items])
        return items

    @staticmethod
    def _f(fields: dict, key: str):
        return fields.get(key.encode(), fields.get(key))

    async def consume_features(self, max_n: int = 500, timeout: float = 0.5) -> list[dict]:
        await self._ensure()
        items = await self._read("eg:features", max_n, int(timeout * 1000))
        return [json.loads(self._f(f, "m")) for _, f in items]

    async def consume_frames(self, max_n: int = 32, timeout: float = 0.5) -> list[dict]:
        await self._ensure()
        out = []
        for stream, block in (("eg:frames:hi", 1), ("eg:frames:lo", int(timeout * 1000))):
            if len(out) >= max_n:
                break
            for _, f in await self._read(stream, max_n - len(out), block if not out else 1):
                m = json.loads(self._f(f, "m"))
                m["jpeg"] = self._f(f, "jpeg")
                out.append(m)
        return out

    def stats(self) -> dict:
        return {"kind": self.kind, "consumer": self.consumer, "at": time.time()}

    async def close(self) -> None:
        await self.r.aclose()


class Hub:
    """Đẩy cập nhật tới dashboard giám thị. Chỉ gửi khi điểm rủi ro/trạng thái thay đổi."""

    def __init__(self):
        self.subs: dict[int, set] = {}  # exam_id -> {websocket}

    def subscribe(self, exam_id: int, ws) -> None:
        self.subs.setdefault(exam_id, set()).add(ws)

    def unsubscribe(self, exam_id: int, ws) -> None:
        self.subs.get(exam_id, set()).discard(ws)

    async def publish(self, exam_id: int, msg: dict) -> None:
        dead = []
        for ws in list(self.subs.get(exam_id, ())):
            try:
                await ws.send_json(msg)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.unsubscribe(exam_id, ws)
