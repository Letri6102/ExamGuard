# %% [markdown]
# # 03 · Mô hình hợp nhất đa phương thức theo thời gian (C1)
#
# **Mục tiêu:** từ vector 54 đặc trưng mỗi 2 giây (khuôn mặt, danh tính, vật thể, âm thanh, trình duyệt, nhịp gõ, bài làm,
# hệ thống) dự đoán xác suất gian lận **theo dòng thời gian (streaming)**, học được từ **nhãn yếu** (chỉ biết đoạn/phiên có
# gian lận), và **chịu được khi thiếu phương thức** (máy yếu, không có micro, chưa có YOLO…).
#
# **Kiến trúc** (`eg_common.build_fusion_model`): encoder riêng từng phương thức → cổng hợp nhất (che phương thức thiếu)
# → GRU nhân quả (mỗi bước = một cửa sổ) → xác suất. Xuất ONNX **một bước** (`x, h → prob, h_out, gates`) để server chạy
# streaming; cổng `gates` dùng làm giải thích cho giám thị.
#
# **Dữ liệu:**
# 1. **OEP** (Atoum et al., 24 người, video webcam + nhãn khoảng thời gian gian lận) — trích đặc trưng khuôn mặt (BlazeFace),
#    âm thanh (Silero VAD), vật thể (YOLO từ notebook 02) bằng đúng công thức của trình duyệt. Tải từ Kaggle (cần `kaggle.json`).
#    Trang gốc: cvlab.cse.msu.edu/oep-dataset.html — hãy trích dẫn bài báo OEP khi dùng.
# 2. **Phiên thi tổng hợp** có chèn đoạn gian lận (rời tab tra cứu, điện thoại, người nhắc, màn hình phụ, dán, chép câu trả lời LLM)
#    và các tình huống vô hại dễ báo nhầm (thông báo hệ thống, ho, mất mạng, máy yếu). Cửa sổ nhịp gõ lấy từ dữ liệu thật
#    của notebook 01 nếu có. **Nêu rõ trong bài báo phần nào dùng dữ liệu tổng hợp.**
# 3. (Tùy chọn) Dữ liệu xuất từ trang giám thị ExamGuard (đặc trưng + nhãn xác nhận/bác cờ, mã giả danh) để tinh chỉnh.
#
# **Đánh giá:** chia theo người; AUC, recall @ FPR 1% / 5% mức cửa sổ và mức phiên; so với baseline luật ngưỡng (đang chạy
# trên server khi chưa có mô hình); ablation từng phương thức, bỏ modality dropout, bỏ cổng, bỏ tiền huấn luyện;
# ngưỡng theo hạng máy (C5).
#
# Thời gian: không có OEP ~15 phút (GPU T4); có OEP thêm ~30–60 phút trích đặc trưng.

# %%
!pip -q install -U kaggle  # bản mới mới đọc được API token dạng KGAT_...
!pip -q install mediapipe silero-vad onnx onnxruntime opencv-python-headless pyarrow

# %%
import json
import math
import os
import random
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from eg_common import (DIM, EPISODES, FEATURES, MOD_OF, MOD_SLICES, MODALITIES, SCALES, WINDOW_S, build_fusion_model,
                       check_onnx_parity, export_fusion_onnx, make_synthetic_sessions, model_input, rule_score_matrix,
                       summarize)

OUT = Path("/content/examguard")
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = Path("/content/drive/MyDrive/examguard")
except Exception as e:
    print("Không mount được Drive:", e)
OUT.mkdir(parents=True, exist_ok=True)
DATA = Path("/content/data") if Path("/content").exists() else Path("data")
DATA.mkdir(parents=True, exist_ok=True)
DEV = "cuda" if torch.cuda.is_available() else "cpu"
SEED = 0
rng = np.random.default_rng(SEED)
torch.manual_seed(SEED)
random.seed(SEED)
FI = {n: i for i, n in enumerate(FEATURES)}
print("Thiết bị:", DEV, "| đầu vào mô hình:", DIM, "chiều")

# %% [markdown]
# ## 1. OEP: tải từ Kaggle một lần, lưu trên Google Drive
# - **Lần đầu:** cần khóa Kaggle. Cách dễ nhất: Kaggle → ảnh đại diện → Settings → API → **Generate New Token**, bấm nút
#   sao chép mã `KGAT_...`, rồi dán vào ô nhập hiện ra khi chạy ô dưới (ô nhập ẩn ký tự) và Enter.
#   Cách khác: thêm secret tên `KAGGLE_API_TOKEN` ở biểu tượng 🔑 bên trái Colab (bật "Notebook access"), hoặc để trống
#   ô nhập rồi Enter để tải file `kaggle.json` kiểu cũ.
#   Ô này tải bộ OEP (~12 GB nén), chỉ giữ phần cần dùng (video webcam `*1.avi`, âm thanh `*.wav`, nhãn `gt.txt`, ~4 GB)
#   và chép vào `MyDrive/examguard_data/oep/`. Khóa Kaggle được lưu vào `MyDrive/examguard_data/` để lần sau khỏi nhập lại.
# - **Các lần sau:** chép từ Drive về ổ máy Colab (nhanh hơn tải lại), không cần Kaggle nữa.
# - Cần ~5 GB trống trên Drive. Video đeo kính (`*2.avi`, ~8 GB) không dùng nên không lưu; đặt `KEEP_WEARCAM = True` nếu cần.
# - Đặc trưng đã trích được lưu ở `MyDrive/examguard/oep_features.npz`; khi file này có rồi thì không cần video nữa.
#   Xóa file đó nếu muốn trích lại (ví dụ sau khi huấn luyện lại YOLO ở notebook 02).
#
# Không có khóa Kaggle thì đặt `USE_OEP = False` (vẫn huấn luyện được trên phiên tổng hợp, nhưng kết quả **không** đủ cho bài báo).

# %%
import shutil
import zipfile

USE_OEP = True
KEEP_WEARCAM = False
DRIVE_DATA = OUT.parent / "examguard_data"  # MyDrive/examguard_data khi đã mount Drive
DRIVE_OEP = DRIVE_DATA / "oep"
DRIVE_OK = str(OUT).startswith("/content/drive")
OEP_DIR = DATA / "oep"


def _keep(name: str) -> bool:
    base = name.rsplit("/", 1)[-1].lower()
    if base in ("gt.txt", "read_me.txt") or base.endswith(".wav") or base.endswith("1.avi"):
        return True
    return KEEP_WEARCAM and base.endswith(".avi")


def _kaggle_ready() -> bool:
    """Tìm khóa Kaggle: biến môi trường / Colab Secrets / file trong ~/.kaggle / bản lưu trên Drive / hỏi người dùng."""
    home = Path.home() / ".kaggle"
    home.mkdir(exist_ok=True)
    tok_file, kj = home / "access_token", home / "kaggle.json"
    saved_tok, saved_kj = DRIVE_DATA / "access_token", DRIVE_DATA / "kaggle.json"
    token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if not token:
        try:
            from google.colab import userdata
            token = (userdata.get("KAGGLE_API_TOKEN") or "").strip()
        except Exception:
            pass
    for src, dst in ((saved_tok, tok_file), (saved_kj, kj)):
        if not dst.exists() and src.exists():
            shutil.copy(src, dst)
    if not token and tok_file.exists():
        token = tok_file.read_text().strip()
    if not token and not kj.exists():
        from getpass import getpass
        token = getpass("Dán API token Kaggle (KGAT_...) rồi Enter; để trống để tải file kaggle.json: ").strip()
        if not token:
            try:
                from google.colab import files
                print("Chọn file kaggle.json")
                up = files.upload()
                kj.write_bytes(up["kaggle.json"])
            except Exception as e:
                print("Không có khóa Kaggle:", e)
                return False
    if token:
        os.environ["KAGGLE_API_TOKEN"] = token
        tok_file.write_text(token)
        tok_file.chmod(0o600)
    if kj.exists():
        kj.chmod(0o600)
    if DRIVE_OK:
        DRIVE_DATA.mkdir(parents=True, exist_ok=True)
        for src, dst in ((tok_file, saved_tok), (kj, saved_kj)):
            if src.exists() and not dst.exists():
                shutil.copy(src, dst)
                print("Đã lưu khóa Kaggle vào", dst)
    return True


def _drive_has_oep() -> bool:
    return (DRIVE_OEP / "_done").exists()


if USE_OEP and not any(OEP_DIR.rglob("gt.txt")):
    if DRIVE_OK and _drive_has_oep():
        print("Chép OEP từ Drive về máy Colab...")
        shutil.copytree(DRIVE_OEP, OEP_DIR, dirs_exist_ok=True)
    elif _kaggle_ready():
        zpath = DATA / "oep-dataset.zip"
        if not zpath.exists():
            print("Tải OEP từ Kaggle (~12 GB, khoảng 5–15 phút)...")
            os.system(f"kaggle datasets download -d raajanwankhade/oep-dataset -p {DATA} -q")
        if zpath.exists():
            with zipfile.ZipFile(zpath) as z:
                members = [m for m in z.namelist() if not m.endswith("/") and _keep(m)]
                print("Giải nén", len(members), "file cần dùng...")
                for m in members:
                    rel = Path(*Path(m).parts[1:]) if len(Path(m).parts) > 1 else Path(m)  # bỏ thư mục "OEP database/"
                    dst = OEP_DIR / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(m) as src, open(dst, "wb") as f:
                        shutil.copyfileobj(src, f, 1 << 24)
            zpath.unlink()  # giải phóng ổ Colab
            if DRIVE_OK:
                print("Chép vào Drive:", DRIVE_OEP, "(vài phút)...")
                shutil.copytree(OEP_DIR, DRIVE_OEP, dirs_exist_ok=True)
                (DRIVE_OEP / "_done").write_text("ok")
            else:
                print("Không có Drive: dữ liệu chỉ nằm trên máy Colab, sẽ mất khi ngắt phiên.")
        else:
            print("Tải thất bại. Khóa Kaggle sai hoặc hết hạn: tạo token mới trên Kaggle rồi chạy lại ô này để nhập lại.")
            for f in (Path.home() / ".kaggle" / "access_token", Path.home() / ".kaggle" / "kaggle.json",
                      DRIVE_DATA / "access_token", DRIVE_DATA / "kaggle.json"):
                f.unlink(missing_ok=True)
            os.environ.pop("KAGGLE_API_TOKEN", None)
subjects = sorted({p.parent for p in OEP_DIR.rglob("gt.txt")}) if USE_OEP else []
USE_OEP = USE_OEP and len(subjects) > 0
print("OEP:", len(subjects), "người", "(TẮT — chỉ dùng dữ liệu tổng hợp)" if not USE_OEP else "")
if subjects:
    print("Ví dụ gt.txt:", (subjects[0] / "gt.txt").read_text()[:200])

# %% [markdown]
# ### Đọc nhãn `gt.txt`
# Mỗi dòng: `bắt_đầu kết_thúc loại`. Thời gian dạng `MMSS` (vd `0123` = 1 phút 23 giây) hoặc `mm:ss` hoặc giây.
# **Kiểm tra lại** với vài dòng in ở trên trước khi chạy tiếp.

# %%
def parse_time(tok: str) -> float:
    tok = tok.strip()
    if ":" in tok:
        parts = [float(x) for x in tok.split(":")]
        return sum(v * 60 ** (len(parts) - 1 - i) for i, v in enumerate(parts))
    if tok.isdigit() and len(tok) in (3, 4):
        return int(tok[:-2]) * 60 + int(tok[-2:])
    return float(tok)


def parse_gt(path: Path) -> list[tuple[float, float, str]]:
    out = []
    for line in path.read_text(errors="ignore").splitlines():
        toks = line.replace(",", " ").split()
        if len(toks) >= 2:
            try:
                a, b = parse_time(toks[0]), parse_time(toks[1])
            except ValueError:
                continue
            if b >= a:
                out.append((a, b, toks[2] if len(toks) > 2 else "?"))
    return out


if USE_OEP:
    for s in subjects[:3]:
        print(s.name, parse_gt(s / "gt.txt")[:4])

# %% [markdown]
# ## 2. Trích đặc trưng OEP bằng đúng công thức của trình duyệt
# - Khuôn mặt: MediaPipe BlazeFace short-range, 1 khung/giây → `face_window` (6 điểm mốc → yaw/pitch).
# - Âm thanh: Silero VAD trên âm thanh video, khung 100 ms → `audio_window`.
# - Vật thể: YOLO (notebook 02) trên khung 320 px đã làm mờ mặt, mỗi 5 giây; giữ kết quả 15 giây như server.
# - Danh tính / trình duyệt / nhịp gõ / bài làm: OEP không có → mask = 0 (đúng tình huống "thiếu phương thức");
#   ở mục 4 sẽ ghép thêm nền trình duyệt + bài làm bình thường để không lệch phân bố.

# %%
import subprocess
import urllib.request

import cv2

from eg_common import audio_window, face_window, head_pose

BLAZE = DATA / "blaze_face_short_range.tflite"
if USE_OEP and not BLAZE.exists():
    urllib.request.urlretrieve("https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite", BLAZE)
YOLO_ONNX = OUT / "proctor_yolo.onnx"  # từ notebook 02 (không có thì bỏ qua vật thể)


def make_face_detector():
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    return vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(BLAZE)), min_detection_confidence=0.5)), mp


def make_yolo():
    if not YOLO_ONNX.exists():
        return None
    import ast

    import onnxruntime as ort
    sess = ort.InferenceSession(str(YOLO_ONNX), providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    names = {int(k): v for k, v in ast.literal_eval(sess.get_modelmeta().custom_metadata_map["names"]).items()}
    return sess, names


GROUPS = {"obj_phone": ["cell phone"], "obj_book": ["book"], "obj_headphone": ["headphones"], "obj_screen": ["laptop", "tv"]}


def yolo_groups(yolo, img320) -> dict:
    sess, names = yolo
    h, w = img320.shape[:2]
    canvas = np.full((320, 320, 3), 114, np.uint8)
    canvas[:h, :w] = img320
    x = canvas[..., ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255
    o = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]
    cls = o[4:]
    res = {g: float(max((cls[i].max() for i, n in names.items() if n in labs), default=0.0)) for g, labs in GROUPS.items()}
    pid = [i for i, n in names.items() if n == "person"]
    if pid:
        sc = np.sort(cls[pid[0]])[::-1]
        res["obj_person_extra"] = float(sc[1]) if len(sc) > 1 and sc[1] > 0.25 else 0.0  # gần đúng (chưa NMS)
    return res


def pixelate(img, dets, W, H):
    for d in dets:
        b = d.bounding_box
        cx, cy = b.origin_x + b.width / 2, b.origin_y + b.height / 2
        bw, bh = b.width * 1.5, b.height * 1.7
        x0, y0 = int(max(0, cx - bw / 2)), int(max(0, cy - bh / 2))
        x1, y1 = int(min(W, cx + bw / 2)), int(min(H, cy + bh / 2))
        if x1 - x0 > 2 and y1 - y0 > 2:
            img[y0:y1, x0:x1] = cv2.resize(cv2.resize(img[y0:y1, x0:x1], (6, 8)), (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
    return img


def vad_frames(video: Path) -> tuple[np.ndarray, np.ndarray]:
    """Cờ có tiếng nói + RMS mỗi 100 ms. OEP ghi micro thành file .wav riêng; không có thì lấy âm thanh trong video."""
    from silero_vad import get_speech_timestamps, load_silero_vad
    src = next(iter(sorted(video.parent.glob("*.wav"))), video)
    wav = DATA / (video.stem + "_16k.wav")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(wav)], check=False)
    if not wav.exists():
        return np.zeros(0, bool), np.zeros(0)
    import soundfile as sf
    audio, sr = sf.read(str(wav), dtype="float32")
    model = load_silero_vad()
    ts = get_speech_timestamps(torch.from_numpy(audio), model, sampling_rate=sr, return_seconds=True)
    n = int(len(audio) / sr * 10)
    flags = np.zeros(n, bool)
    for t in ts:
        flags[int(t["start"] * 10):int(t["end"] * 10)] = True
    rms = np.array([np.sqrt(np.mean(audio[i * 1600:(i + 1) * 1600] ** 2) + 1e-12) for i in range(n)])
    return flags, rms


def extract_oep(subject: Path) -> dict | None:
    vids = sorted(subject.glob("*1.avi")) or sorted(subject.glob("*.avi"))  # *1.avi = webcam
    if not vids:
        return None
    video = vids[0]
    det, mp = make_face_detector()
    yolo = make_yolo()
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = n_frames / fps
    T = int(dur // WINDOW_S)
    face_samples = [[] for _ in range(T)]
    obj = {}  # nhóm -> (xác suất, thời điểm)
    obj_rows = [None] * T
    last_frame_t = -1e9
    for sec in range(int(dur)):
        cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        H, W = frame.shape[:2]
        res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        dets = sorted(res.detections, key=lambda d: -d.bounding_box.width * d.bounding_box.height)
        if dets:
            b = dets[0].bounding_box
            kps = [(k.x, k.y) for k in dets[0].keypoints]
            yaw, pitch = head_pose(kps)
            s = {"count": len(dets), "yaw": yaw, "pitch": pitch, "cx": (b.origin_x + b.width / 2) / W,
                 "cy": (b.origin_y + b.height / 2) / H, "size": b.width * b.height / (W * H), "conf": dets[0].categories[0].score}
        else:
            s = {"count": 0, "yaw": 0, "pitch": 0, "cx": 0, "cy": 0, "size": 0, "conf": 0}
        k = int(sec // WINDOW_S)
        if k < T:
            face_samples[k].append(s)
        if yolo and sec % 5 == 0:
            small = cv2.resize(pixelate(frame.copy(), dets, W, H), (320, int(320 * H / W)))
            for g, v in yolo_groups(yolo, small).items():
                obj[g] = (v, sec)
            last_frame_t = sec
        if k < T:
            obj_rows[k] = {**{g: (v if sec - t <= 15 else 0.0) for g, (v, t) in obj.items()},
                           "obj_age": min(sec - last_frame_t, 30)}
    cap.release()
    flags, rms = vad_frames(video)
    gt = parse_gt(subject / "gt.txt")
    X, y = [], []
    for k in range(T):
        v = face_window(face_samples[k]) if face_samples[k] else {}
        a0, a1 = int(k * WINDOW_S * 10), int((k + 1) * WINDOW_S * 10)
        has_audio = len(flags) >= a1
        if has_audio:
            v.update(audio_window(list(flags[a0:a1]), list(rms[a0:a1])))
        has_obj = yolo is not None and obj_rows[k] is not None
        if has_obj:
            v.update(obj_rows[k])
        masks = {"face": bool(face_samples[k]), "audio": has_audio, "object": has_obj}
        X.append(model_input(v, masks))
        t0, t1 = k * WINDOW_S, (k + 1) * WINDOW_S
        y.append(int(any(a < t1 and b > t0 for a, b, _ in gt)))
    return {"id": f"oep:{subject.name}", "X": np.stack(X), "y": np.array(y), "tier": "mid", "source": "oep"}


oep_sessions = []
if USE_OEP:
    cache = OUT / "oep_features.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        oep_sessions = list(z["sessions"])
    else:
        from tqdm.auto import tqdm
        for s in tqdm(subjects):
            r = extract_oep(s)
            if r:
                oep_sessions.append(r)
        np.savez_compressed(cache, sessions=np.array(oep_sessions, dtype=object))
    print(len(oep_sessions), "phiên OEP; tỉ lệ cửa sổ gian lận:",
          round(float(np.mean(np.concatenate([s["y"] for s in oep_sessions]))), 3))

# %% [markdown]
# ## 3. Phiên thi tổng hợp
# Mỗi phiên = chuỗi cửa sổ 2 giây. Hành vi bình thường có nhiễu thực tế (nhìn ra ngoài thoáng qua, thông báo làm mất focus,
# ho, mất mạng, máy yếu bỏ khung). Phiên gian lận có 1–3 đoạn thuộc các kiểu dưới đây. Cửa sổ nhịp gõ lấy từ dữ liệu thật
# (notebook 01: B cho bình thường; T/FT cho chép câu trả lời LLM) nếu có file `keystroke_windows.parquet`.

# %%
KS = OUT / "keystroke_windows.parquet"
KFEATS = [f for f in FEATURES if MOD_OF[f] == "keystroke"]
if KS.exists():
    kw = pd.read_parquet(KS)
    KS_BONA = kw[kw.label == "B"][KFEATS].values.astype(np.float32)
    KS_COPY = kw[kw.label.isin(["T", "FT", "P", "FP"])][KFEATS].values.astype(np.float32)
    print("Nhịp gõ thật:", len(KS_BONA), "cửa sổ bình thường,", len(KS_COPY), "cửa sổ có hỗ trợ LLM")
else:
    KS_BONA = KS_COPY = None
    print("Không có keystroke_windows.parquet: dùng nhịp gõ mô phỏng (chạy notebook 01 để có dữ liệu thật).")


N_SYN, T_SYN = 600, 450  # 600 phiên × 15 phút
syn_sessions = make_synthetic_sessions(N_SYN, T_SYN, rng, ks_bona=KS_BONA, ks_copy=KS_COPY)
sessions = syn_sessions + oep_sessions
print(len(sessions), "phiên; tỉ lệ cửa sổ gian lận", round(float(np.mean(np.concatenate([s["y"] for s in sessions]))), 3))

# %% [markdown]
# ## 4. (Tùy chọn) Dữ liệu thật từ ExamGuard
# Trang **Thống kê → "Xuất đặc trưng + nhãn (giả danh)"** tạo CSV: mỗi dòng một cửa sổ; `window_label` = 1 (giám thị xác
# nhận cờ) / 0 (bác cờ hoặc phiên được rà soát là sạch) / trống (chưa biết). Tải CSV lên Drive rồi đặt đường dẫn.

# %%
PLATFORM_CSV = OUT / "features_exam.csv"
platform_sessions = []
if PLATFORM_CSV.exists():
    pdf = pd.read_csv(PLATFORM_CSV)
    for sid, g in pdf.groupby("session"):
        g = g.sort_values("t")
        vals = g[FEATURES].values.astype(np.float32)
        mk = g[[f"mask_{m}" for m in MODALITIES]].values.astype(np.float32)
        X = np.concatenate([np.clip(vals / SCALES, -5, 5) * np.repeat(mk, [MOD_SLICES[m].stop - MOD_SLICES[m].start for m in MODALITIES], 1), mk], 1)
        yl = g.window_label.values
        platform_sessions.append({"id": f"exam:{sid}", "X": X, "y": np.nan_to_num(yl.astype(float), nan=-1).astype(np.int64),
                                  "tier": g.tier.iloc[0], "source": "platform"})
    print(len(platform_sessions), "phiên thật từ ExamGuard (nhãn -1 = chưa biết)")

# %% [markdown]
# ## 5. Huấn luyện
# - **Tiền huấn luyện tự giám sát:** che ngẫu nhiên một phương thức, học tái tạo đặc trưng bị che (không cần nhãn).
# - **Tinh chỉnh MIL:** chia phiên thành đoạn 150 cửa sổ (5 phút). Mất mát = BCE theo cửa sổ (nơi có nhãn) + BCE mức túi
#   (túi dương nếu đoạn có gian lận, điểm túi = log-sum-exp mềm của điểm cửa sổ).
# - **Modality dropout:** mỗi đoạn tắt ngẫu nhiên từng phương thức với xác suất 0,3 (bắt buộc vì máy yếu / thiếu tín hiệu).

# %%
from torch import nn

CHUNK = 150
BATCH = 32
NF = len(FEATURES)
MOD_WIDTH = [MOD_SLICES[m].stop - MOD_SLICES[m].start for m in MODALITIES]


def chunks(sess_list, chunk=CHUNK, stride=CHUNK // 2):
    xs, ys = [], []
    for s in sess_list:
        X, y = s["X"], s["y"]
        for a in range(0, max(1, len(X) - chunk + 1), stride):
            xc, yc = X[a:a + chunk], y[a:a + chunk]
            if len(xc) < chunk:
                xc = np.concatenate([xc, np.zeros((chunk - len(xc), DIM), np.float32)])
                yc = np.concatenate([yc, -np.ones(chunk - len(yc), np.int64)])
            xs.append(xc)
            ys.append(yc)
    return torch.tensor(np.stack(xs)), torch.tensor(np.stack(ys))


def modality_dropout(x, p=0.3):
    if p <= 0:
        return x
    B = x.shape[0]
    drop = (torch.rand(B, 1, len(MODALITIES), device=x.device) < p).float()
    keep = 1 - drop
    feat_keep = torch.repeat_interleave(keep, torch.tensor(MOD_WIDTH, device=x.device), dim=-1)
    x = x.clone()
    x[..., :NF] *= feat_keep
    x[..., NF:] *= keep
    return x


def pretrain(model, X, epochs=3, lr=2e-3):
    opt = torch.optim.AdamW(model.parameters(), lr)
    for ep in range(epochs):
        tot = 0
        for idx in torch.randperm(len(X)).split(BATCH):
            x = X[idx].to(DEV)
            m = torch.randint(0, len(MODALITIES), (len(x),), device=DEV)
            keep = torch.ones(len(x), 1, len(MODALITIES), device=DEV)
            keep[torch.arange(len(x)), 0, m] = 0
            feat_keep = torch.repeat_interleave(keep, torch.tensor(MOD_WIDTH, device=DEV), -1)
            xin = x.clone()
            xin[..., :NF] *= feat_keep
            xin[..., NF:] *= keep
            _, _, rec = model(xin)
            target_mask = (1 - feat_keep) * torch.repeat_interleave(x[..., NF:], torch.tensor(MOD_WIDTH, device=DEV), -1)
            loss = (((rec - x[..., :NF]) ** 2) * target_mask).sum() / target_mask.sum().clamp(min=1)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item()
        print(f"  tiền huấn luyện {ep + 1}/{epochs}: {tot:.3f}")


def finetune(model, X, Y, epochs=10, lr=2e-3, p_drop=0.3, bag_w=1.0):
    opt = torch.optim.AdamW(model.parameters(), lr, weight_decay=1e-4)
    pos = (Y == 1).float().sum()
    neg = (Y == 0).float().sum()
    pw = torch.tensor(float(neg / pos.clamp(min=1)), device=DEV).clamp(max=20)
    for ep in range(epochs):
        model.train()
        tot = 0
        for idx in torch.randperm(len(X)).split(BATCH):
            x, y = modality_dropout(X[idx].to(DEV), p_drop), Y[idx].to(DEV)
            logits, _, _ = model(x)
            lab = y >= 0
            w_loss = nn.functional.binary_cross_entropy_with_logits(logits[lab], y[lab].float(), pos_weight=pw) if lab.any() else 0
            bag_y = (y == 1).any(1).float()
            bag_known = lab.any(1)
            bag_logit = torch.logsumexp(logits.masked_fill(~lab, -1e4), 1) - math.log(CHUNK)
            b_loss = nn.functional.binary_cross_entropy_with_logits(bag_logit[bag_known], bag_y[bag_known]) if bag_known.any() else 0
            loss = w_loss + bag_w * b_loss
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += float(loss)
        print(f"  tinh chỉnh {ep + 1}/{epochs}: {tot:.3f}")
    return model.eval()


@torch.no_grad()
def predict_session(model, X: np.ndarray) -> np.ndarray:
    """Streaming từ đầu đến cuối phiên (giống server)."""
    logits, _, _ = model(torch.tensor(X[None]).to(DEV))
    return torch.sigmoid(logits)[0].cpu().numpy()


def server_risk(p: np.ndarray, decay=0.9) -> np.ndarray:
    """Giống backend: risk_t = max(decay·risk_{t-1}, 100·p_t)."""
    r, out = 0.0, []
    for v in p:
        r = max(decay * r, 100 * float(v))
        out.append(r)
    return np.array(out)

# %% [markdown]
# ## 6. Đánh giá chéo theo người (5 fold) + baseline luật ngưỡng + ablation

# %%
from sklearn.model_selection import GroupKFold

RUN_ABLATIONS = True
groups = np.array([s["id"] for s in sessions])


def run_fold(train_s, test_s, pretrain_on=True, p_drop=0.3, only_modality=None, no_gate=False):
    model = build_fusion_model().to(DEV)
    if no_gate:
        model.gate.weight.data.zero_()
        model.gate.bias.data.zero_()
        model.gate.requires_grad_(False)
    def restrict(sl):
        if only_modality is None:
            return sl
        out = []
        for s in sl:
            X = s["X"].copy()
            for k, m in enumerate(MODALITIES):
                if m != only_modality:
                    X[:, MOD_SLICES[m]] = 0
                    X[:, NF + k] = 0
            out.append({**s, "X": X})
        return out
    train_s, test_s = restrict(train_s), restrict(test_s)
    Xc, Yc = chunks(train_s + platform_sessions)
    if pretrain_on:
        pretrain(model, Xc)
    finetune(model, Xc, Yc, p_drop=p_drop)
    return model, [(s, predict_session(model, s["X"])) for s in test_s]


def collect(preds, key="model"):
    yw, sw, ys, ss, rows = [], [], [], [], []
    for s, p in preds:
        m = s["y"] >= 0
        yw.append(s["y"][m])
        sw.append(p[m])
        ys.append(int((s["y"] == 1).any()))
        ss.append(float(server_risk(p).max()))
    return {"window": summarize(np.concatenate(yw), np.concatenate(sw)), "session": summarize(np.array(ys), np.array(ss))}


configs = {"Hợp nhất (đầy đủ)": {}}
if RUN_ABLATIONS:
    configs.update({"Bỏ tiền huấn luyện": {"pretrain_on": False}, "Bỏ modality dropout": {"p_drop": 0.0},
                    "Bỏ cổng hợp nhất": {"no_gate": True}})
    configs.update({f"Chỉ {m}": {"only_modality": m} for m in ["face", "object", "audio", "browser", "keystroke"]})

all_preds = {k: [] for k in configs}
rule_preds = []
gkf = GroupKFold(n_splits=5)
for fold, (tr, te) in enumerate(gkf.split(sessions, groups=groups)):
    print(f"Fold {fold + 1}/5")
    tr_s = [sessions[i] for i in tr]
    te_s = [sessions[i] for i in te]
    for name, kw in configs.items():
        _, preds = run_fold(tr_s, te_s, **kw)
        all_preds[name] += preds
    rule_preds += [(s, rule_score_matrix(s["X"])) for s in te_s]

rows = []
for name, preds in [("Baseline luật ngưỡng", rule_preds)] + list(all_preds.items()):
    r = collect(preds)
    rows.append({"cấu hình": name, **{f"cửa sổ {k}": v for k, v in r["window"].items() if k not in ("n", "pos")},
                 **{f"phiên {k}": v for k, v in r["session"].items() if k not in ("n", "pos")}})
report = pd.DataFrame(rows)
report.to_csv(OUT / "results_fusion.csv", index=False)
report

# %% [markdown]
# ### Theo nguồn dữ liệu và theo kiểu gian lận (mô hình đầy đủ)

# %%
by = []
for src in ("oep", "synthetic"):
    sub = [(s, p) for s, p in all_preds["Hợp nhất (đầy đủ)"] if s["source"] == src]
    if sub:
        by.append({"nguồn": src, **collect(sub)["window"]})
for kind in EPISODES:
    sub = [(s, p) for s, p in all_preds["Hợp nhất (đầy đủ)"] if s["source"] == "synthetic" and (kind in s.get("kinds", []) or not s["y"].any())]
    by.append({"kiểu": kind, **collect(sub)["session"]})
pd.DataFrame(by)

# %% [markdown]
# ## 7. Ngưỡng theo hạng máy (C5)
# Ngưỡng chung có thể làm máy yếu bị gắn cờ nhầm nhiều hơn. Tìm ngưỡng điểm rủi ro (thang 0–100 như server) để FPR mức phiên
# bằng nhau giữa các hạng máy, rồi đặt vào `backend/.env`: `TIER_THRESHOLDS=low:..,mid:..,high:..`.

# %%
TARGET_FPR = 0.05
preds = all_preds["Hợp nhất (đầy đủ)"]
tiers = {}
for tier in ("low", "mid", "high"):
    neg = [server_risk(p).max() for s, p in preds if s["tier"] == tier and not (s["y"] == 1).any()]
    pos = [server_risk(p).max() for s, p in preds if s["tier"] == tier and (s["y"] == 1).any()]
    if len(neg) < 10:
        continue
    thr = float(np.quantile(neg, 1 - TARGET_FPR))
    global_fpr = float(np.mean(np.array(neg) >= 60))
    tiers[tier] = {"threshold": round(thr, 1), "fpr@60": round(global_fpr, 3), "fpr@thr": round(float(np.mean(np.array(neg) >= thr)), 3),
                   "recall@thr": round(float(np.mean(np.array(pos) >= thr)), 3) if pos else None}
print(pd.DataFrame(tiers).T)
print("\nTIER_THRESHOLDS=" + ",".join(f"{k}:{v['threshold']}" for k, v in tiers.items()))

# %% [markdown]
# ## 8. Huấn luyện mô hình cuối trên toàn bộ dữ liệu và xuất ONNX cho server
# Server (`backend/app/services/risk.py`) nhận: input `x[1, 62]`, `h[1, 64]` → output `prob`, `h_out`, `gates`.
# Ô dưới còn kiểm tra ONNX chạy từng bước cho kết quả khớp PyTorch chạy cả chuỗi.

# %%
final = build_fusion_model().to(DEV)
Xc, Yc = chunks(sessions + platform_sessions)
pretrain(final, Xc)
finetune(final, Xc, Yc)
final = final.cpu().eval()
onnx_path = str(OUT / "fusion_gru.onnx")
export_fusion_onnx(final, onnx_path)
err = check_onnx_parity(final, onnx_path, sessions[0]["X"][:200])
print("Sai khác ONNX vs PyTorch:", err)
json.dump({"report": report.to_dict(orient="records"), "tiers": tiers, "n_sessions": len(sessions),
           "oep_sessions": len(oep_sessions), "platform_sessions": len(platform_sessions)},
          open(OUT / "fusion_report.json", "w"), ensure_ascii=False, indent=1, default=str)
print("Chép file này vào ExamGuard/backend/models/fusion_gru.onnx:", onnx_path)
