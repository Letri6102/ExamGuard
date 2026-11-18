from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def fake_facereg_transport(state: dict) -> httpx.MockTransport:
    """FaceReg giả lập: by-token trả kết quả check-in; /api/monitor trả trạng thái do test đặt."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/api/admin/checkins/by-token/"):
            if req.headers.get("X-Admin-Token") != "fr-admin":
                return httpx.Response(401)
            tok = req.url.path.rsplit("/", 1)[1]
            info = state["tokens"].get(tok)
            return httpx.Response(200, json=info) if info else httpx.Response(404, json={"detail": "x"})
        if req.url.path == "/api/monitor":
            state["monitor_calls"] += 1
            return httpx.Response(200, json={"status": state["monitor_status"], "identity_status": state["monitor_status"],
                                             "score": state["monitor_score"], "face_count": 1})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    monkeypatch.setenv("FACEREG_URL", "http://facereg.test")
    monkeypatch.setenv("FACEREG_ADMIN_TOKEN", "fr-admin")
    monkeypatch.setenv("YOLO_MODEL", str(tmp_path / "none.onnx"))
    monkeypatch.setenv("FUSION_MODEL", str(tmp_path / "none.onnx"))
    monkeypatch.setenv("EVIDENCE_FRAMES", "flagged")
    return tmp_path


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from app.config import load_settings
    from app.main import create_app
    from app.services.facereg import FaceRegClient

    fr_state = {"tokens": {}, "monitor_status": "OK", "monitor_score": 0.8, "monitor_calls": 0}
    app = create_app(load_settings())
    with TestClient(app) as c:
        app.state.facereg = FaceRegClient(app.state.settings, transport=fake_facereg_transport(fr_state))
        c.fr = fr_state
        yield c


def wait_for(fn, timeout=8.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("hết thời gian chờ")
