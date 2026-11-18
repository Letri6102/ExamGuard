"""Luồng đầy đủ: quản trị tạo kỳ thi -> thí sinh check-in FaceReg -> làm bài -> đặc trưng -> cờ -> giám thị."""
from __future__ import annotations

import base64
import io
import json

import cv2
import numpy as np
from sqlalchemy import select

from conftest import wait_for


def admin_headers(c):
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def setup_exam(c, H, require_facereg=True):
    qs = []
    payloads = [
        {"type": "single", "stem": "Thủ đô của Việt Nam?", "options": ["Hà Nội", "Huế", "Đà Nẵng", "Cần Thơ"], "answer": ["A"]},
        {"type": "multi", "stem": "Số nguyên tố?", "options": ["2", "3", "4", "9"], "answer": ["A", "B"]},
        {"type": "numeric", "stem": "Tính {{a}} * {{b}}", "params": {"a": {"min": 2, "max": 9}, "b": {"min": 2, "max": 9}},
         "answer": "a*b", "variants": ["Nhân {{a}} với {{b}} được bao nhiêu?"]},
        {"type": "essay", "stem": "Viết đoạn văn ngắn về mùa thu."},
    ]
    for p in payloads:
        r = c.post("/api/admin/questions", json=p, headers=H)
        assert r.status_code == 200, r.text
        qs.append(r.json()["id"])
    bad = c.post("/api/admin/questions", json={"type": "numeric", "stem": "{{__import__('os')}}", "answer": "1"}, headers=H)
    assert bad.status_code == 422
    r = c.post("/api/admin/exams", json={"code": "T1", "title": "Thi thử", "duration_minutes": 20,
                                         "require_facereg": require_facereg}, headers=H)
    exam_id = r.json()["id"]
    assert c.post(f"/api/admin/exams/{exam_id}/status", json={"status": "open"}, headers=H).status_code == 409  # chưa có câu
    c.put(f"/api/admin/exams/{exam_id}/questions", json={"question_ids": qs}, headers=H)
    csv_data = "code,full_name\nSV1,Nguyễn Văn A\nSV2,Trần Thị B\n"
    r = c.post(f"/api/admin/exams/{exam_id}/candidates/import", files={"file": ("ds.csv", csv_data.encode())}, headers=H)
    assert r.json() == {"added": 2, "updated": 0}
    assert c.post(f"/api/admin/exams/{exam_id}/status", json={"status": "open"}, headers=H).status_code == 200
    roster = {x["code"]: x["access_code"] for x in c.get(f"/api/admin/exams/{exam_id}/candidates", headers=H).json()}
    return exam_id, roster


def candidate_login(c, code, access):
    r = c.post("/api/auth/candidate-login", json={"exam_code": "T1", "candidate_code": code, "access_code": access})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["token"]


def jpeg_bytes(w=320, h=240):
    img = np.full((h, w, 3), 128, np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


def window(t, **v):
    base = {"face_count_mean": 1, "face_count_max": 1, "face_missing_ratio": 0, "face_conf": 0.9, "keys": 0}
    base.update(v)
    return {"t": t, "v": base, "m": {"face": 1, "audio": 1, "browser": 1, "keystroke": 1, "answer": 1, "system": 1}, "ev": []}


def test_full_flow(client):
    c = client
    H = admin_headers(c)
    exam_id, roster = setup_exam(c, H)

    # --- đăng nhập thí sinh, sai mã bị chặn
    assert c.post("/api/auth/candidate-login", json={"exam_code": "T1", "candidate_code": "SV1", "access_code": "XXXX"}).status_code == 401
    CH, ctoken = candidate_login(c, "SV1", roster["SV1"])
    st = c.get("/api/exam/state", headers=CH).json()
    assert st["facereg"]["required"] and "code=SV1" in st["facereg"]["checkin_url"]
    assert c.post("/api/exam/device", json={"cores": 2, "memory_gb": 4, "face_ms": 50}, headers=CH).json()["tier"] == "low"

    # --- chưa xác thực thì không được bắt đầu; không được bỏ qua FaceReg khi bắt buộc
    assert c.post("/api/exam/start", headers=CH).status_code == 409
    assert c.post("/api/exam/facereg/skip", headers=CH).status_code == 403

    # --- token FaceReg của người khác bị từ chối
    c.fr["tokens"]["tok-other"] = {"candidate_code": "SV2", "exam_code": "T1", "final_status": "ACCEPT", "checkin_id": 9, "score": 0.9}
    assert c.post("/api/exam/facereg/callback", json={"ftoken": "tok-other"}, headers=CH).status_code == 403
    c.fr["tokens"]["tok-sv1-abc"] = {"candidate_code": "SV1", "exam_code": "T1", "final_status": "ACCEPT", "checkin_id": 1, "score": 0.9}
    r = c.post("/api/exam/facereg/callback", json={"ftoken": "tok-sv1-abc"}, headers=CH).json()
    assert r["status"] == "verified"

    # --- bắt đầu, lấy đề, trả lời đúng hết phần trắc nghiệm/số
    assert c.post("/api/exam/start", headers=CH).json()["status"] == "in_progress"
    from app import db as dbmod
    from app.models import Attempt, PaperItem, Question

    with dbmod.SessionLocal() as db:
        att = db.scalar(select(Attempt))
        items = db.scalars(select(PaperItem).where(PaperItem.attempt_id == att.id).order_by(PaperItem.position)).all()
        qmap = {q.id: q for q in db.scalars(select(Question))}
    for it in items:
        q = c.get(f"/api/exam/question/{it.position}", headers=CH).json()
        assert "{{" not in q["stem"] and "answer" in q
        qq = qmap[it.question_id]
        perm = json.loads(it.option_perm_json)
        params = json.loads(it.params_json)
        if qq.qtype == "single":
            val = perm.index("A")
        elif qq.qtype == "multi":
            val = [perm.index("A"), perm.index("B")]
        elif qq.qtype == "numeric":
            val = params["a"] * params["b"]
            assert str(params["a"]) in q["stem"]
        else:
            val = "Mùa thu Hà Nội..."
        r = c.post("/api/exam/answer", json={"position": it.position, "value": val, "client_seq": 1, "t": 10 + it.position}, headers=CH)
        assert r.json()["ok"], r.text
    # gửi lại cùng client_seq (gửi bù sau mất mạng) -> không tính là đổi đáp án
    assert c.post("/api/exam/answer", json={"position": 0, "value": 0, "client_seq": 1}, headers=CH).json().get("duplicate")

    # --- WebSocket: 3 cửa sổ bình thường, rồi rời tab + gõ nhiều sau khi quay lại -> cờ
    with c.websocket_connect(f"/ws/exam?token={ctoken}") as ws:
        ws.send_json({"type": "windows", "windows": [window(i) for i in range(3)]})
        assert ws.receive_json()["t"] == 2
        ws.send_json({"type": "frame", "hi": True, "t": 6.0, "jpeg": base64.b64encode(jpeg_bytes()).decode(), "id": 1})
        assert ws.receive_json()["ok"]
        ws.send_json({"type": "windows", "windows": [window(3, hidden_sec=2, tab_switches=1, chars_after_return=45,
                                                             paste_attempts=1)]})
        ws.receive_json()

    def flagged():
        rows = c.get(f"/api/proctor/exams/{exam_id}/attempts", headers=H).json()
        return rows[0] if rows[0]["open_flags"] else None
    row = wait_for(flagged)
    assert row["risk"] >= 70 and row["tier"] == "low"

    # --- xác thực lại: FaceReg báo MISMATCH -> cửa sổ sau có id_mismatch
    c.fr["monitor_status"], c.fr["monitor_score"] = "MISMATCH", 0.1
    r = c.post("/api/exam/reverify", files={"image": ("f.jpg", jpeg_bytes(128, 128), "image/jpeg")}, data={"reason": "reappear"},
               headers=CH).json()
    assert r["status"] == "MISMATCH" and c.fr["monitor_calls"] == 1
    c.post("/api/exam/telemetry", json={"windows": [window(4)]}, headers=CH)

    def mismatch_recorded():
        d = c.get(f"/api/proctor/attempts/{row['id']}", headers=H).json()
        return d if any("ID_MISMATCH" in w["r"] for w in d["timeline"]) else None
    detail = wait_for(mismatch_recorded)
    assert detail["identity"][0]["status"] == "MISMATCH"
    assert detail["modality_coverage"]["object"] == 0  # chưa có YOLO
    flag = detail["flags"][0]
    assert any("Rời tab" in r[0] for r in flag["reasons"])
    assert flag["evidence"], "khung chùm sự kiện được giữ làm bằng chứng"

    # --- xem bằng chứng được ghi nhật ký; giám thị xác nhận cờ -> nhãn khi xuất dữ liệu
    ev = c.get(f"/api/proctor/flags/{flag['id']}/evidence/{flag['evidence'][0]}", headers=H)
    assert ev.status_code == 200 and ev.headers["content-type"] == "image/jpeg"
    assert c.get(f"/api/proctor/flags/{flag['id']}/evidence/..%2F..%2Fx.jpg", headers=H).status_code == 404
    c.post(f"/api/proctor/flags/{flag['id']}/review", json={"status": "confirmed", "note": "đã xem"}, headers=H)
    audit = c.get("/api/admin/audit", headers=H).json()
    assert any(a["action"] == "view_evidence" for a in audit)
    csv_text = c.get(f"/api/proctor/exams/{exam_id}/export.csv", headers=H).text
    lines = csv_text.strip().splitlines()
    header = lines[0].split(",")
    assert "SV1" not in csv_text and "Nguyễn" not in csv_text  # chỉ mã giả danh
    li = header.index("window_label")
    assert any(l.split(",")[li] == "1" for l in lines[1:])

    # --- nộp bài, chấm điểm (3 câu tự chấm đúng, tự luận chấm tay)
    assert c.post("/api/exam/submit", headers=CH).json()["status"] == "submitted"
    with dbmod.SessionLocal() as db:
        assert db.scalar(select(Attempt)).score == 3.0
    m = c.get(f"/api/proctor/exams/{exam_id}/metrics", headers=H).json()
    assert m["flags_by_status"]["confirmed"] >= 1 and m["by_tier"]["low"]["flagged"] == 1
    sys_stats = c.get("/api/analysis/system", headers=H).json()
    assert sys_stats["latency_p95"] is not None and sys_stats["latency_p95"] < 5

    # --- truy vết lộ đề: chép lại đề của SV1 -> SV1 đứng đầu
    CH2, _ = candidate_login(c, "SV2", roster["SV2"])
    c.fr["tokens"]["tok-sv2-xyz"] = {"candidate_code": "SV2", "exam_code": "T1", "final_status": "ACCEPT", "checkin_id": 2, "score": 0.9}
    c.post("/api/exam/facereg/callback", json={"ftoken": "tok-sv2-xyz"}, headers=CH2)
    c.post("/api/exam/start", headers=CH2)
    with dbmod.SessionLocal() as db:
        from app.services.paper import render_item
        att1 = db.scalars(select(Attempt).order_by(Attempt.id)).first()
        items = db.scalars(select(PaperItem).where(PaperItem.attempt_id == att1.id).order_by(PaperItem.position)).all()
        leaked = "\n".join(f"Câu {i + 1}. {r.stem}\n" + "\n".join(f"{chr(65 + k)}. {o}" for k, o in enumerate(r.options))
                           for i, r in enumerate(render_item(it, qmap[it.question_id]) for it in items))
    res = c.post(f"/api/analysis/exams/{exam_id}/leak-trace", json={"text": leaked + f"\n{att1.watermark}"}, headers=H).json()
    assert res["watermark_matches"][0]["candidate_code"] == "SV1"
    assert res["content_ranking"][0]["candidate_code"] == "SV1"


def test_proctor_cannot_use_admin_api(client):
    c = client
    H = admin_headers(c)
    c.post("/api/admin/users", json={"username": "gt1", "password": "matkhau123", "role": "proctor"}, headers=H)
    r = c.post("/api/auth/login", json={"username": "gt1", "password": "matkhau123"})
    PH = {"Authorization": "Bearer " + r.json()["token"]}
    assert c.get("/api/admin/questions", headers=PH).status_code == 403
    assert c.get("/api/proctor/exams", headers=PH).status_code == 200
    assert c.get("/api/proctor/exams").status_code == 401


def test_optional_facereg_skip(client, monkeypatch):
    c = client
    H = admin_headers(c)
    exam_id, roster = setup_exam(c, H, require_facereg=False)
    CH, _ = candidate_login(c, "SV2", roster["SV2"])
    assert c.post("/api/exam/facereg/skip", headers=CH).json()["status"] == "verified"
    assert c.post("/api/exam/start", headers=CH).status_code == 200
