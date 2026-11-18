"""Điểm rủi ro theo từng cửa sổ 2 giây.

Hai bộ chấm:
  - RuleScorer: luật ngưỡng có giải thích (baseline trong bài báo, chạy khi chưa có mô hình).
  - FusionScorer: mô hình hợp nhất đa phương thức dạng streaming (GRU nhân quả) xuất ONNX từ
    notebooks/03_fusion_mil.ipynb. Mỗi phiên giữ trạng thái ẩn h; mỗi cửa sổ chạy 1 bước.

Điểm phiên: risk_t = max(decay * risk_{t-1}, 100 * window_score) -> phản ứng nhanh với sự kiện mạnh,
giảm dần khi bình thường. Gắn cờ khi risk >= ngưỡng theo hạng máy (C5: công bằng theo thiết bị).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

log = logging.getLogger("examguard.risk")


class FeatureSpec:
    def __init__(self, schema: dict):
        self.features = schema["features"]
        self.names = [f["name"] for f in self.features]
        self.modalities = schema["modalities"]
        self.scales = np.array([float(f["scale"]) for f in self.features], np.float32)
        self.mod_of = {f["name"]: f["modality"] for f in self.features}
        self.index = {n: i for i, n in enumerate(self.names)}
        self.dim = len(self.names) + len(self.modalities)

    def vector(self, values: dict[str, float]) -> np.ndarray:
        return np.array([float(values.get(n, 0.0) or 0.0) for n in self.names], np.float32)

    def model_input(self, values: dict[str, float], masks: dict[str, int]) -> np.ndarray:
        x = np.clip(self.vector(values) / self.scales, -5, 5)
        m = np.array([1.0 if masks.get(mod) else 0.0 for mod in self.modalities], np.float32)
        x = x * np.repeat(m, [sum(1 for f in self.features if f["modality"] == mod) for mod in self.modalities])
        return np.concatenate([x, m]).astype(np.float32)


@dataclass
class Rule:
    code: str
    modality: str
    weight: float
    label: str
    test: Callable[[dict], bool]


RULES: list[Rule] = [
    Rule("MULTI_FACE", "face", 0.60, "Có từ 2 khuôn mặt trong khung hình", lambda v: v["face_count_max"] >= 2),
    Rule("NO_FACE", "face", 0.25, "Không thấy khuôn mặt", lambda v: v["face_missing_ratio"] >= 0.99),
    Rule("HEAD_AWAY", "face", 0.15, "Quay đầu khỏi màn hình", lambda v: v["head_away_ratio"] >= 0.5),
    Rule("ID_MISMATCH", "identity", 0.70, "Khuôn mặt không khớp ảnh đăng ký", lambda v: v["id_mismatch"] >= 1),
    Rule("PHONE", "object", 0.60, "Phát hiện điện thoại", lambda v: v["obj_phone"] >= 0.5),
    Rule("PERSON_EXTRA", "object", 0.50, "Có người thứ hai (theo thân người)", lambda v: v["obj_person_extra"] >= 0.5),
    Rule("HEADPHONE", "object", 0.35, "Phát hiện tai nghe", lambda v: v["obj_headphone"] >= 0.5),
    Rule("BOOK", "object", 0.30, "Phát hiện sách/giấy", lambda v: v["obj_book"] >= 0.5),
    Rule("SCREEN", "object", 0.30, "Phát hiện màn hình/laptop phụ", lambda v: v["obj_screen"] >= 0.5),
    Rule("SPEECH", "audio", 0.20, "Có tiếng nói kéo dài", lambda v: v["speech_ratio"] >= 0.3),
    Rule("TAB_AWAY", "browser", 0.40, "Rời tab/cửa sổ làm bài",
         lambda v: v["hidden_sec"] > 0 or v["tab_switches"] > 0),
    Rule("BLUR", "browser", 0.20, "Cửa sổ làm bài mất focus", lambda v: v["blur_sec"] >= 1),
    Rule("FULLSCREEN_EXIT", "browser", 0.20, "Thoát chế độ toàn màn hình",
         lambda v: v["fullscreen_exits"] > 0 or v["not_fullscreen_sec"] >= 2),
    Rule("WINDOW_SMALL", "browser", 0.15, "Thu nhỏ/chia đôi cửa sổ", lambda v: v["window_small"] >= 1),
    Rule("EXTENDED_SCREEN", "browser", 0.20, "Đang dùng màn hình mở rộng", lambda v: v["screen_extended"] >= 1),
    Rule("PASTE", "browser", 0.40, "Cố dán/kéo thả nội dung",
         lambda v: v["paste_attempts"] > 0 or v["drop_attempts"] > 0),
    Rule("COPY", "browser", 0.25, "Cố sao chép đề", lambda v: v["copy_attempts"] > 0),
    Rule("TYPE_AFTER_RETURN", "browser", 0.40, "Gõ nhiều ngay sau khi quay lại tab",
         lambda v: v["chars_after_return"] >= 20),
    Rule("TRANSCRIBE_RHYTHM", "keystroke", 0.20, "Nhịp gõ đều, ít sửa (giống chép lại)",
         lambda v: v["keys"] >= 10 and v["pause_ratio"] < 0.02 and v["backspace_ratio"] < 0.02
         and v["burst_ratio"] > 0.6 and v["flight_cv"] < 0.5),
    Rule("FAST_ANSWER", "answer", 0.15, "Trả lời nhanh bất thường so với độ khó",
         lambda v: v["time_vs_expected"] <= -2.5),
]


class RuleScorer:
    name = "rules"

    def score(self, values: dict, masks: dict) -> tuple[float, list[dict]]:
        p_clean = 1.0
        reasons = []
        for r in RULES:
            if not masks.get(r.modality):
                continue
            try:
                hit = r.test(values)
            except KeyError:
                hit = False
            if hit:
                p_clean *= 1 - r.weight
                reasons.append({"code": r.code, "label": r.label, "modality": r.modality, "weight": r.weight})
        reasons.sort(key=lambda x: -x["weight"])
        return 1 - p_clean, reasons


class FusionScorer:
    """ONNX: inputs x[1,D], h[1,H] -> prob[1,1], h_out[1,H], gates[1,M] (mức đóng góp từng phương thức)."""
    name = "fusion"

    def __init__(self, path: Path, spec: FeatureSpec):
        import onnxruntime as ort

        self.sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self.spec = spec
        ins = {i.name: i for i in self.sess.get_inputs()}
        if "x" not in ins or "h" not in ins:
            raise ValueError("Mô hình hợp nhất phải có input 'x' và 'h'")
        dim = ins["x"].shape[-1]
        if isinstance(dim, int) and dim != spec.dim:
            raise ValueError(f"Mô hình nhận {dim} chiều nhưng schema có {spec.dim}")
        self.hidden = int(ins["h"].shape[-1])
        self.outputs = [o.name for o in self.sess.get_outputs()]
        self.rules = RuleScorer()

    def init_state(self) -> np.ndarray:
        return np.zeros((1, self.hidden), np.float32)

    def score(self, values: dict, masks: dict, h: np.ndarray) -> tuple[float, list[dict], np.ndarray]:
        x = self.spec.model_input(values, masks)[None]
        out = dict(zip(self.outputs, self.sess.run(None, {"x": x, "h": h})))
        prob = float(np.asarray(out["prob"]).reshape(-1)[0])
        reasons = self.rules.score(values, masks)[1]  # giải thích dễ đọc cho giám thị
        if "gates" in out:
            g = np.asarray(out["gates"]).reshape(-1)
            top = np.argsort(-g)[:3]
            reasons.append({"code": "MODEL", "label": "Mô hình: đóng góp chính từ "
                            + ", ".join(f"{self.spec.modalities[i]} ({g[i]:.2f})" for i in top),
                            "modality": "model", "weight": round(prob, 3)})
        return prob, reasons, np.asarray(out["h_out"], np.float32).reshape(1, -1)


def load_scorer(path: Path, spec: FeatureSpec):
    if path and Path(path).exists():
        try:
            s = FusionScorer(path, spec)
            log.info("Dùng mô hình hợp nhất %s (hidden=%d)", path, s.hidden)
            return s
        except Exception as e:  # noqa: BLE001
            log.warning("Không nạp được mô hình hợp nhất (%s); dùng luật ngưỡng", e)
    return RuleScorer()


def update_risk(prev: float, window_score: float, decay: float = 0.9) -> float:
    return round(max(decay * prev, 100.0 * window_score), 2)
