"""Mã dùng chung cho các notebook ExamGuard (được nhúng vào notebook bằng %%writefile để chạy độc lập trên Colab).

Các hàm đặc trưng ở đây là bản Python của frontend/src/monitor/features.ts — PHẢI giữ khớp công thức,
vì mô hình huấn luyện ở notebook sẽ chạy trên đặc trưng do trình duyệt thí sinh tính.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

# ----------------------------------------------------------------------------- schema
_HERE = Path(__file__).resolve().parent
SCHEMA_PATH = next(p for p in (_HERE / "feature_schema.json", _HERE.parent / "shared" / "feature_schema.json") if p.exists())
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
FEATURES = [f["name"] for f in SCHEMA["features"]]
MODALITIES = SCHEMA["modalities"]
SCALES = np.array([float(f["scale"]) for f in SCHEMA["features"]], np.float32)
MOD_OF = {f["name"]: f["modality"] for f in SCHEMA["features"]}
MOD_SLICES = {}
for _m in MODALITIES:
    _idx = [i for i, f in enumerate(SCHEMA["features"]) if f["modality"] == _m]
    MOD_SLICES[_m] = slice(_idx[0], _idx[-1] + 1)
DIM = len(FEATURES) + len(MODALITIES)
WINDOW_S = SCHEMA["window_seconds"]


def model_input(values: dict, masks: dict) -> np.ndarray:
    """Giống backend FeatureSpec.model_input: clip(x/scale) * mask + mask."""
    x = np.array([float(values.get(n, 0.0) or 0.0) for n in FEATURES], np.float32)
    x = np.clip(x / SCALES, -5, 5)
    m = np.array([1.0 if masks.get(mod) else 0.0 for mod in MODALITIES], np.float32)
    for k, mod in enumerate(MODALITIES):
        x[MOD_SLICES[mod]] *= m[k]
    return np.concatenate([x, m]).astype(np.float32)


# ----------------------------------------------------------------------------- khuôn mặt
def head_pose(kps) -> tuple[float, float]:
    """kps: 6 điểm BlazeFace chuẩn hóa [(x,y)...]: mắt phải, mắt trái, mũi, miệng, tai phải, tai trái."""
    if kps is None or len(kps) < 4:
        return 0.0, 0.0
    re, le, nose, mouth = [np.asarray(k[:2], float) for k in kps[:4]]
    mid = (re + le) / 2
    eye = max(float(np.hypot(*(le - re))), 1e-6)
    roll = math.atan2(le[1] - re[1], le[0] - re[0])
    c, s = math.cos(-roll), math.sin(-roll)

    def rot(p):
        d = p - mid
        return np.array([d[0] * c - d[1] * s, d[0] * s + d[1] * c])

    n, m = rot(nose), rot(mouth)
    yaw = math.degrees(math.atan(n[0] / eye / 0.6))
    pitch = (n[1] / max(m[1], 1e-6) - 0.55) * 120
    return float(np.clip(yaw, -90, 90)), float(np.clip(pitch, -60, 60))


def face_window(samples: list[dict]) -> dict:
    """samples: [{count, yaw, pitch, cx, cy, size, conf}] trong một cửa sổ 2 giây."""
    n = len(samples) or 1
    pres = [s for s in samples if s["count"] > 0]
    yaws = [s["yaw"] for s in pres]
    pit = [s["pitch"] for s in pres]
    shift = sum(math.hypot(pres[i]["cx"] - pres[i - 1]["cx"], pres[i]["cy"] - pres[i - 1]["cy"]) for i in range(1, len(pres)))
    sd = lambda a: float(np.std(a, ddof=1)) if len(a) > 1 else 0.0  # noqa: E731
    return {
        "face_count_mean": float(np.mean([s["count"] for s in samples])) if samples else 0.0,
        "face_count_max": max([s["count"] for s in samples], default=0),
        "face_missing_ratio": (len(samples) - len(pres)) / n if samples else 0.0,
        "yaw_mean": float(np.mean(yaws)) if yaws else 0.0, "yaw_std": sd(yaws),
        "pitch_mean": float(np.mean(pit)) if pit else 0.0, "pitch_std": sd(pit),
        "head_away_ratio": (sum(1 for s in pres if abs(s["yaw"]) > 30 or abs(s["pitch"]) > 25) / len(pres)) if pres else 0.0,
        "box_shift": shift,
        "face_size": float(np.mean([s["size"] for s in pres])) if pres else 0.0,
        "face_conf": float(np.mean([s["conf"] for s in pres])) if pres else 0.0,
    }


# ----------------------------------------------------------------------------- nhịp gõ
def key_timings(events: list[tuple]) -> tuple[list, list, int, int]:
    """events: [(t_ms, 'down'|'up', code, ime_bool)] -> dwell, flight, số phím, số phím sửa."""
    down, dwell, flight = {}, [], []
    last, keys, edits = None, 0, 0
    for t, typ, code, ime in events:
        if ime:
            continue
        if typ == "down":
            if code in down:
                continue
            down[code] = t
            keys += 1
            if code in ("Backspace", "Delete"):
                edits += 1
            if last is not None:
                flight.append(t - last)
            last = t
        else:
            t0 = down.pop(code, None)
            if t0 is not None:
                dwell.append(t - t0)
    return [d for d in dwell if 0 < d < 2000], [f for f in flight if 0 < f < 30000], keys, edits


def keystroke_window(events, compositions: int, inserted_chars: int, seconds: float, base: dict | None) -> dict:
    dwell, flight, keys, edits = key_timings(events)
    fl = [f for f in flight if f < 5000]
    dm = float(np.mean(dwell)) if dwell else 0.0
    fm = float(np.mean(fl)) if fl else 0.0
    sd = lambda a: float(np.std(a, ddof=1)) if len(a) > 1 else 0.0  # noqa: E731
    return {
        "keys": keys,
        "dwell_rel": (dm / base["dwell"] if base and dm else (1.0 if dm else 0.0)),
        "dwell_cv": sd(dwell) / dm if dm else 0.0,
        "flight_rel": (fm / base["flight"] if base and fm else (1.0 if fm else 0.0)),
        "flight_cv": sd(fl) / fm if fm else 0.0,
        "burst_ratio": sum(f < 150 for f in flight) / len(flight) if flight else 0.0,
        "pause_ratio": sum(f > 2000 for f in flight) / len(flight) if flight else 0.0,
        "backspace_ratio": edits / keys if keys else 0.0,
        "compositions": compositions,
        "chars_per_sec": inserted_chars / max(seconds, 1e-3),
    }


PRIVATE_KEYSTROKE = ["dwell_rel", "dwell_cv", "flight_rel", "flight_cv", "burst_ratio", "pause_ratio"]


def privatize(v: dict, epsilon: float, step: float = 0.05, rng: np.random.Generator | None = None) -> dict:
    """Lượng tử hóa + nhiễu Laplace(b = step/ε) như trên máy thí sinh. ε = inf -> chỉ lượng tử hóa."""
    rng = rng or np.random.default_rng()
    out = dict(v)
    for k in PRIVATE_KEYSTROKE:
        if k in out:
            noise = 0.0 if not np.isfinite(epsilon) else rng.laplace(0, step / max(epsilon, 1e-6))
            out[k] = max(0.0, round((out[k] + noise) / step) * step)
    return out


_NON_CHAR = {"backspace", "delete", "shift", "shiftleft", "shiftright", "control", "controlleft", "controlright", "ctrl",
             "alt", "altleft", "altright", "meta", "metaleft", "metaright", "tab", "enter", "capslock", "escape", "esc",
             "arrowup", "arrowdown", "arrowleft", "arrowright", "home", "end", "pageup", "pagedown", "insert"}


def is_char_key(code) -> bool:
    c = str(code).lower()
    return c not in _NON_CHAR and not c.startswith(("f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9"))


def session_keystroke_windows(events: list[tuple], warmup_s: float = 60, epsilon: float = float("inf"),
                              window_s: float = WINDOW_S, rng=None) -> list[dict]:
    """Cắt chuỗi phím (t_ms tuyệt đối) thành cửa sổ 2 giây với chuẩn hóa theo đoạn khởi động, như trình duyệt."""
    if not events:
        return []
    events = sorted(events, key=lambda e: e[0])
    t0 = events[0][0]
    base, warm_d, warm_f = None, [], []
    out, cur, k = [], [], 0
    end = events[-1][0]
    w_start = t0
    i = 0
    while w_start <= end:
        w_end = w_start + window_s * 1000
        cur = []
        while i < len(events) and events[i][0] < w_end:
            cur.append(events[i])
            i += 1
        if base is None:
            d, f, _, _ = key_timings(cur)
            warm_d += d
            warm_f += [x for x in f if x < 5000]
            if (w_end - t0) / 1000 >= warmup_s and len(warm_d) >= 30 and len(warm_f) >= 30:
                base = {"dwell": float(np.median(warm_d)), "flight": float(np.median(warm_f))}
        chars = sum(1 for e in cur if e[1] == "down" and not e[3] and is_char_key(e[2]))
        w = keystroke_window(cur, 0, chars, window_s, base)
        w = privatize(w, epsilon, rng=rng) if epsilon is not None else w
        d, f, _, _ = key_timings(cur)
        # thời gian tuyệt đối (ms): chỉ dùng để đánh giá rủi ro tái nhận dạng ở notebook 04 — trình duyệt KHÔNG gửi
        w["_dwell_ms"] = float(np.mean(d)) if d else 0.0
        w["_flight_ms"] = float(np.mean([x for x in f if x < 5000])) if f else 0.0
        w["_dwell_ms_std"] = float(np.std(d)) if len(d) > 1 else 0.0
        out.append(w)
        w_start = w_end
        k += 1
    return out


# ----------------------------------------------------------------------------- âm thanh
def audio_window(speech_flags: list[bool], rms: list[float]) -> dict:
    onsets = sum(1 for i, s in enumerate(speech_flags) if s and (i == 0 or not speech_flags[i - 1]))
    r = float(np.mean(rms)) if rms else 0.0
    return {"speech_ratio": float(np.mean(speech_flags)) if speech_flags else 0.0, "speech_segments": onsets,
            "audio_energy": float(np.clip((20 * math.log10(r) + 60) / 60, 0, 1)) if r > 0 else 0.0}


def time_vs_expected(seconds: float, expected: float) -> float:
    return float(np.clip((seconds - expected) / (0.5 * expected), -5, 5)) if expected else 0.0


# ----------------------------------------------------------------------------- baseline luật ngưỡng (khớp backend)
RULES = [
    ("MULTI_FACE", "face", 0.60, lambda v: v["face_count_max"] >= 2),
    ("NO_FACE", "face", 0.25, lambda v: v["face_missing_ratio"] >= 0.99),
    ("HEAD_AWAY", "face", 0.15, lambda v: v["head_away_ratio"] >= 0.5),
    ("ID_MISMATCH", "identity", 0.70, lambda v: v["id_mismatch"] >= 1),
    ("PHONE", "object", 0.60, lambda v: v["obj_phone"] >= 0.5),
    ("PERSON_EXTRA", "object", 0.50, lambda v: v["obj_person_extra"] >= 0.5),
    ("HEADPHONE", "object", 0.35, lambda v: v["obj_headphone"] >= 0.5),
    ("BOOK", "object", 0.30, lambda v: v["obj_book"] >= 0.5),
    ("SCREEN", "object", 0.30, lambda v: v["obj_screen"] >= 0.5),
    ("SPEECH", "audio", 0.20, lambda v: v["speech_ratio"] >= 0.3),
    ("TAB_AWAY", "browser", 0.40, lambda v: v["hidden_sec"] > 0 or v["tab_switches"] > 0),
    ("BLUR", "browser", 0.20, lambda v: v["blur_sec"] >= 1),
    ("FULLSCREEN_EXIT", "browser", 0.20, lambda v: v["fullscreen_exits"] > 0 or v["not_fullscreen_sec"] >= 2),
    ("WINDOW_SMALL", "browser", 0.15, lambda v: v["window_small"] >= 1),
    ("EXTENDED_SCREEN", "browser", 0.20, lambda v: v["screen_extended"] >= 1),
    ("PASTE", "browser", 0.40, lambda v: v["paste_attempts"] > 0 or v["drop_attempts"] > 0),
    ("COPY", "browser", 0.25, lambda v: v["copy_attempts"] > 0),
    ("TYPE_AFTER_RETURN", "browser", 0.40, lambda v: v["chars_after_return"] >= 20),
    ("TRANSCRIBE_RHYTHM", "keystroke", 0.20, lambda v: v["keys"] >= 10 and v["pause_ratio"] < 0.02
     and v["backspace_ratio"] < 0.02 and v["burst_ratio"] > 0.6 and v["flight_cv"] < 0.5),
    ("FAST_ANSWER", "answer", 0.15, lambda v: v["time_vs_expected"] <= -2.5),
]


def rule_score(values: dict, masks: dict) -> float:
    v = {n: float(values.get(n, 0.0)) for n in FEATURES}
    p = 1.0
    for _, mod, w, test in RULES:
        if masks.get(mod) and test(v):
            p *= 1 - w
    return 1 - p


def rule_score_matrix(X: np.ndarray) -> np.ndarray:
    """X: [N, DIM] (đầu vào mô hình) -> điểm luật. Giải chuẩn hóa bằng scale."""
    out = np.zeros(len(X), np.float32)
    for i, x in enumerate(X):
        vals = dict(zip(FEATURES, (x[:len(FEATURES)] * SCALES).tolist()))
        masks = {m: x[len(FEATURES) + k] > 0.5 for k, m in enumerate(MODALITIES)}
        out[i] = rule_score(vals, masks)
    return out


# ----------------------------------------------------------------------------- đánh giá
def recall_at_fpr(y: np.ndarray, s: np.ndarray, fpr: float) -> float:
    y, s = np.asarray(y).astype(bool), np.asarray(s, float)
    neg = np.sort(s[~y])[::-1]
    if not len(neg) or not y.any():
        return float("nan")
    k = int(np.floor(fpr * len(neg)))
    thr = neg[k] if k < len(neg) else -np.inf
    return float((s[y] > thr).mean())


def summarize(y, s) -> dict:
    from sklearn.metrics import average_precision_score, roc_auc_score

    y = np.asarray(y).astype(int)
    if y.min() == y.max():
        return {"n": len(y), "pos": int(y.sum())}
    return {"n": len(y), "pos": int(y.sum()), "auc": round(roc_auc_score(y, s), 4), "ap": round(average_precision_score(y, s), 4),
            "recall@fpr1%": round(recall_at_fpr(y, s, 0.01), 4), "recall@fpr5%": round(recall_at_fpr(y, s, 0.05), 4)}


# ----------------------------------------------------------------------------- mô hình hợp nhất (PyTorch)
def build_fusion_model(hidden: int = 64, d: int = 32, dropout: float = 0.1):
    import torch
    from torch import nn

    class FusionGRU(nn.Module):
        """Encoder riêng từng phương thức -> cổng (gated fusion, che phương thức thiếu) -> GRU nhân quả -> xác suất.

        Một bước = một cửa sổ 2 giây; trạng thái ẩn h mang thông tin quá khứ (streaming)."""

        def __init__(self):
            super().__init__()
            self.slices = [MOD_SLICES[m] for m in MODALITIES]
            self.enc = nn.ModuleList([nn.Sequential(nn.Linear(s.stop - s.start, d), nn.GELU(), nn.Linear(d, d))
                                      for s in self.slices])
            self.gate = nn.Linear(len(MODALITIES) * d, len(MODALITIES))
            self.drop = nn.Dropout(dropout)
            self.gru = nn.GRUCell(d, hidden)
            self.head = nn.Sequential(nn.Linear(hidden + d, hidden), nn.GELU(), nn.Linear(hidden, 1))
            self.hidden = hidden
            self.recon = nn.Linear(hidden + d, len(FEATURES))  # cho tiền huấn luyện tự giám sát

        def fuse(self, x):
            feats = x[..., :len(FEATURES)]
            mask = x[..., len(FEATURES):]
            z = torch.stack([enc(feats[..., s]) for enc, s in zip(self.enc, self.slices)], dim=-2)  # [..., M, d]
            logits = self.gate(z.flatten(-2)) + (mask - 1) * 1e4  # phương thức thiếu -> cổng = 0
            g = torch.softmax(logits, dim=-1)
            g = g * (mask.sum(-1, keepdim=True) > 0)
            fused = (g.unsqueeze(-1) * z).sum(-2)
            return self.drop(fused), g

        def step(self, x, h):
            fused, g = self.fuse(x)
            h2 = self.gru(fused, h)
            out = torch.cat([h2, fused], -1)
            return torch.sigmoid(self.head(out)).squeeze(-1), h2, g

        def forward(self, xs):  # xs: [B, T, D] -> logits [B, T], gates [B, T, M], recon [B, T, F]
            B, T, _ = xs.shape
            h = xs.new_zeros(B, self.hidden)
            logits, gates, recs = [], [], []
            for t in range(T):
                fused, g = self.fuse(xs[:, t])
                h = self.gru(fused, h)
                out = torch.cat([h, fused], -1)
                logits.append(self.head(out).squeeze(-1))
                gates.append(g)
                recs.append(self.recon(out))
            return torch.stack(logits, 1), torch.stack(gates, 1), torch.stack(recs, 1)

    return FusionGRU()


def export_fusion_onnx(model, path: str) -> None:
    """Xuất MỘT BƯỚC streaming: x[1,D], h[1,H] -> prob[1], h_out[1,H], gates[1,M] (đúng tên backend đọc)."""
    import torch

    class Step(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x, h):
            return self.m.step(x, h)

    model.eval()
    step = Step(model)
    x = torch.zeros(1, DIM)
    h = torch.zeros(1, model.hidden)
    torch.onnx.export(step, (x, h), path, input_names=["x", "h"], output_names=["prob", "h_out", "gates"],
                      opset_version=17, dynamo=False)
    import onnx

    m = onnx.load(path)
    m.ir_version = min(m.ir_version, 9)  # onnxruntime trên server có thể là bản cũ
    m.metadata_props.add(key="schema_version", value=str(SCHEMA["version"]))
    m.metadata_props.add(key="features", value=",".join(FEATURES))
    onnx.save(m, path)


def check_onnx_parity(model, path: str, xs: np.ndarray, atol: float = 1e-4) -> float:
    """Chạy chuỗi bằng ONNX từng bước, so với PyTorch chạy cả chuỗi."""
    import onnxruntime as ort
    import torch

    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    model.eval()
    with torch.no_grad():
        ref = torch.sigmoid(model(torch.from_numpy(xs[None]).float())[0])[0].numpy()
    h = np.zeros((1, model.hidden), np.float32)
    got = []
    for x in xs:
        p, h, _ = sess.run(None, {"x": x[None].astype(np.float32), "h": h})
        got.append(float(np.asarray(p).reshape(-1)[0]))
    err = float(np.max(np.abs(np.array(got) - ref)))
    assert err < atol, err
    return err


# ----------------------------------------------------------------------------- phiên thi tổng hợp
def ks_window(kind: str, rng, ks_bona=None, ks_copy=None) -> np.ndarray:
    pool = ks_bona if kind == "bona" else ks_copy
    if pool is not None and len(pool):
        return pool[rng.integers(len(pool))]
    # mô phỏng: [keys, dwell_rel, dwell_cv, flight_rel, flight_cv, burst, pause, backspace, compositions, cps]
    if kind == "bona":
        k = rng.poisson(8)
        return np.array([k, rng.normal(1, .12), rng.uniform(.3, .7), rng.normal(1, .25), rng.uniform(.6, 1.4),
                         rng.uniform(.1, .4), rng.uniform(0, .15), rng.uniform(.03, .15), rng.poisson(1), k / 2], np.float32)
    k = rng.poisson(14)
    return np.array([k, rng.normal(.95, .08), rng.uniform(.2, .4), rng.normal(.8, .1), rng.uniform(.3, .7),
                     rng.uniform(.4, .8), rng.uniform(0, .03), rng.uniform(0, .03), rng.poisson(1), k / 2], np.float32)


EPISODES = ["tab_lookup", "phone", "second_person", "whisper", "paste", "second_screen", "llm_transcribe"]


def synth_session(sid: int, T: int, cheat: bool, tier: str, rng, ks_bona=None, ks_copy=None) -> dict:
    """Một phiên thi tổng hợp: nền bình thường có nhiễu thực tế + 1–3 đoạn gian lận nếu cheat."""
    FI = {n: i for i, n in enumerate(FEATURES)}
    ks = lambda kind: ks_window(kind, rng, ks_bona, ks_copy)  # noqa: E731
    V = np.zeros((T, len(FEATURES)), np.float32)
    y = np.zeros(T, np.int64)
    f = lambda name: FI[name]  # noqa: E731
    # --- nền bình thường
    miss_p = {"low": .12, "mid": .04, "high": .02}[tier]
    present = rng.random(T) > miss_p
    V[:, f("face_count_mean")] = present
    V[:, f("face_count_max")] = present
    V[:, f("face_missing_ratio")] = ~present
    V[:, f("yaw_mean")] = rng.normal(0, 8, T) * present
    V[:, f("yaw_std")] = np.abs(rng.normal(3, 2, T)) * present
    V[:, f("pitch_mean")] = rng.normal(5, 6, T) * present
    V[:, f("pitch_std")] = np.abs(rng.normal(2, 1.5, T)) * present
    glance = rng.random(T) < .02
    V[glance, f("yaw_mean")] = rng.choice([-1, 1], glance.sum()) * rng.uniform(30, 50, glance.sum())
    V[:, f("head_away_ratio")] = (np.abs(V[:, f("yaw_mean")]) > 30).astype(np.float32)
    V[:, f("box_shift")] = np.abs(rng.normal(.01, .01, T))
    V[:, f("face_size")] = rng.uniform(.06, .14) * present
    V[:, f("face_conf")] = rng.uniform(.8, .97, T) * present
    V[:, f("id_score")] = rng.uniform(.55, .8)
    V[:, f("obj_age")] = rng.uniform(0, 10, T)
    cough = rng.random(T) < .01
    V[:, f("speech_ratio")] = cough * rng.uniform(.1, .3, T)
    V[:, f("speech_segments")] = cough
    V[:, f("audio_energy")] = rng.uniform(.05, .2, T) + cough * .3
    notif = rng.random(T) < .004  # thông báo hệ thống làm mất focus ngắn
    V[notif, f("blur_sec")] = rng.uniform(.3, 1.5, notif.sum())
    typing = rng.random(T) < .35
    for t in np.where(typing)[0]:
        V[t, MOD_SLICES["keystroke"]] = ks("bona")
    V[:, f("question_switches")] = rng.random(T) < .03
    V[:, f("answer_changes")] = rng.random(T) < .05
    V[:, f("time_on_question")] = rng.uniform(0, 120, T)
    V[:, f("progress")] = np.linspace(0, 1, T)
    V[:, f("time_vs_expected")] = np.where(rng.random(T) < .05, rng.normal(0, 1, T), 0)
    lag = {"low": 120, "mid": 40, "high": 15}[tier]
    V[:, f("lag_ms")] = np.abs(rng.normal(lag, lag / 2, T))
    off = rng.random(T) < .003
    V[:, f("offline")] = off
    V[:, f("monitor_load")] = {"low": .12, "mid": .05, "high": .02}[tier]
    # --- phương thức có/không theo phiên
    masks = {"face": 1, "identity": int(rng.random() < .8), "object": int(rng.random() < .7), "audio": int(rng.random() < .85),
             "browser": 1, "keystroke": 1, "answer": 1, "system": 1}
    # --- đoạn gian lận
    kinds = []
    if cheat:
        for _ in range(rng.integers(1, 4)):
            kind = EPISODES[rng.integers(len(EPISODES))]
            L = int(rng.integers(5, 45))
            s0 = int(rng.integers(10, max(11, T - L - 1)))
            sl = slice(s0, s0 + L)
            y[sl] = 1
            kinds.append(kind)
            if kind == "tab_lookup":
                V[sl, f("hidden_sec")] = 2.0
                V[sl, f("tab_switches")] = 0
                V[s0, f("tab_switches")] = 1
                V[sl, f("face_count_mean")] = 1
                after = slice(s0 + L, min(T, s0 + L + 5))
                V[after, f("chars_after_return")] = rng.uniform(20, 80)
                for t in range(after.start, after.stop):
                    V[t, MOD_SLICES["keystroke"]] = ks("copy")
                y[after] = 1
            elif kind == "phone":
                V[sl, f("pitch_mean")] = rng.uniform(25, 45)
                V[sl, f("head_away_ratio")] = 1
                V[sl, f("obj_phone")] = rng.uniform(.4, .9, L) * (rng.random(L) < .6)
            elif kind == "second_person":
                V[sl, f("face_count_max")] = (rng.random(L) < .5) + 1
                V[sl, f("obj_person_extra")] = rng.uniform(.3, .9, L) * (rng.random(L) < .6)
                V[sl, f("speech_ratio")] = rng.uniform(.2, .7, L)
                V[sl, f("speech_segments")] = rng.integers(1, 3, L)
            elif kind == "whisper":
                V[sl, f("speech_ratio")] = rng.uniform(.1, .5, L)
                V[sl, f("audio_energy")] = rng.uniform(.15, .35, L)
            elif kind == "paste":
                V[s0, f("paste_attempts")] = rng.integers(1, 3)
                y[sl] = 0
                y[s0:s0 + 3] = 1
            elif kind == "second_screen":
                V[sl, f("screen_extended")] = 1
                V[sl, f("yaw_mean")] = rng.choice([-1, 1]) * rng.uniform(30, 55, L)
                V[sl, f("head_away_ratio")] = 1
                V[sl, f("obj_screen")] = rng.uniform(.3, .8, L) * (rng.random(L) < .4)
            elif kind == "llm_transcribe":
                for t in range(sl.start, sl.stop):
                    V[t, MOD_SLICES["keystroke"]] = ks("copy")
                V[sl, f("time_vs_expected")] = rng.uniform(-4, -2)
    X = np.stack([model_input(dict(zip(FEATURES, row)), masks) for row in V])
    return {"id": f"syn:{sid}", "X": X, "y": y, "tier": tier, "source": "synthetic", "kinds": kinds}




def make_synthetic_sessions(n: int, T: int, rng, cheat_rate: float = 0.5, tiers=("low", "mid", "high"),
                            tier_p=(0.3, 0.5, 0.2), ks_bona=None, ks_copy=None) -> list[dict]:
    return [synth_session(i, T, cheat=rng.random() < cheat_rate, tier=rng.choice(list(tiers), p=list(tier_p)), rng=rng,
                          ks_bona=ks_bona, ks_copy=ks_copy) for i in range(n)]
