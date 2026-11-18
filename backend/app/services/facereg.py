"""Gọi FaceReg (dịch vụ xác thực khuôn mặt riêng) từ server — ADMIN token không bao giờ ra trình duyệt.

  - check-in: thí sinh mở trang /checkin của FaceReg, xong được chuyển về trang thi kèm `ftoken`;
              server tra `ftoken` bằng GET /api/admin/checkins/by-token/{ftoken} để biết đúng người, đúng kỳ thi.
  - xác thực lại giữa giờ: trình duyệt gửi ẢNH CROP KHUÔN MẶT lên server này; server chuyển sang
              POST /api/monitor của FaceReg, chỉ lưu điểm + trạng thái, ảnh bị bỏ ngay.
"""
from __future__ import annotations

import base64
import logging
from urllib.parse import urlencode

import httpx

from ..config import Settings

log = logging.getLogger("examguard.facereg")


class FaceRegError(Exception):
    pass


class FaceRegClient:
    def __init__(self, s: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = s
        self.enabled = bool(s.facereg_url)
        self._client = httpx.AsyncClient(base_url=s.facereg_url or "http://facereg.invalid", timeout=20,
                                         transport=transport)

    def checkin_url(self, candidate_code: str, exam_code: str) -> str:
        return f"{self.s.facereg_public_url}/checkin?" + urlencode({"code": candidate_code, "exam": exam_code})

    async def resolve_token(self, ftoken: str) -> dict:
        r = await self._client.get(f"/api/admin/checkins/by-token/{ftoken}",
                                   headers={"X-Admin-Token": self.s.facereg_admin_token})
        if r.status_code == 404:
            raise FaceRegError("Không tìm thấy lượt check-in trên FaceReg.")
        if r.status_code != 200:
            raise FaceRegError(f"FaceReg trả lỗi {r.status_code}: {r.text[:200]}")
        return r.json()

    async def reverify(self, ftoken: str, jpeg: bytes) -> dict:
        """-> {status: OK|MISMATCH|NO_FACE|MULTI_FACE|ERROR, score}"""
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        try:
            r = await self._client.post("/api/monitor", json={"token": ftoken, "image": data_url})
        except httpx.HTTPError as e:
            return {"status": "ERROR", "score": None, "detail": str(e)[:200]}
        if r.status_code == 429:
            return {"status": "RATE_LIMITED", "score": None}
        if r.status_code != 200:
            return {"status": "ERROR", "score": None, "detail": r.text[:200]}
        d = r.json()
        return {"status": d.get("identity_status", d.get("status", "ERROR")), "score": d.get("score"),
                "faces": d.get("face_count")}

    async def close(self) -> None:
        await self._client.aclose()
