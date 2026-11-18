# %% [markdown]
# # 02 · YOLO phát hiện vật thể trên khung hình đã làm mờ mặt
#
# Server ExamGuard chạy YOLO trên khung **320 px, mặt đã bị pixel hóa ngay trên máy thí sinh**. Để mô hình quen với đúng
# phân bố này, notebook làm mờ mặt trong ảnh huấn luyện bằng cùng cách (khối 6×8, hộp mặt nới 1,5×1,7) rồi nén JPEG chất lượng 0,7.
#
# **Lớp:** `person` (để đếm người thứ hai), `cell phone`, `book`, `laptop`, `tv` (màn hình phụ), `headphones`.
#
# **Dữ liệu (tự tải bằng FiftyOne):**
# - COCO 2017 — person, cell phone, book, laptop, tv (giấy phép ảnh theo Flickr, nhãn CC BY 4.0).
# - Open Images V7 — Headphones, Mobile phone, Book (nhãn CC BY 4.0) để có lớp tai nghe (COCO không có).
#
# **Đầu ra:** `proctor_yolo.onnx` → chép vào `ExamGuard/backend/models/proctor_yolo.onnx` (server tự đọc tên lớp trong metadata).
#
# Thời gian: tải dữ liệu ~10 phút, huấn luyện YOLO11n 60 epoch ở 320 px ~45–70 phút trên GPU T4.
# Giảm `MAX_PER_CLASS` / `EPOCHS` để chạy thử nhanh.

# %%
!pip -q install ultralytics fiftyone onnx onnxruntime opencv-python-headless

# %%
from pathlib import Path

OUT = Path("/content/examguard")
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = Path("/content/drive/MyDrive/examguard")
except Exception as e:
    print("Không mount được Drive:", e)
OUT.mkdir(parents=True, exist_ok=True)
WORK = Path("/content/yolo") if Path("/content").exists() else Path("yolo_work")
WORK.mkdir(parents=True, exist_ok=True)

CLASSES = ["person", "cell phone", "book", "laptop", "tv", "headphones"]
MAX_PER_CLASS = 2500       # số ảnh tối đa mỗi lớp lấy từ mỗi nguồn
EPOCHS = 60
IMGSZ = 320                # đúng kích thước khung trình duyệt gửi lên
SEED = 0

# %% [markdown]
# ## 1. Tải dữ liệu COCO + Open Images (chỉ những ảnh có lớp cần dùng)

# %%
import fiftyone as fo
import fiftyone.zoo as foz

COCO_CLASSES = ["person", "cell phone", "book", "laptop", "tv"]
OI_MAP = {"Headphones": "headphones", "Mobile phone": "cell phone", "Book": "book", "Laptop": "laptop",
          "Television": "tv", "Person": "person"}

parts = []
for split in ("train", "validation"):
    for c in COCO_CLASSES:
        n = MAX_PER_CLASS if split == "train" else MAX_PER_CLASS // 8
        if c == "person":
            n //= 2  # người rất nhiều trong COCO, lấy ít hơn để cân bằng
        ds = foz.load_zoo_dataset("coco-2017", split=split, label_types=["detections"], classes=[c], max_samples=n,
                                  shuffle=True, seed=SEED, dataset_name=f"coco-{split}-{c}")
        ds.tag_samples(split)
        parts.append(ds)
    oi = foz.load_zoo_dataset("open-images-v7", split=split, label_types=["detections"], classes=["Headphones"],
                              max_samples=MAX_PER_CLASS if split == "train" else MAX_PER_CLASS // 8, shuffle=True, seed=SEED,
                              dataset_name=f"oi-{split}-headphones")
    oi.tag_samples(split)
    parts.append(oi)
print({p.name: len(p) for p in parts})

# %% [markdown]
# ## 2. Gộp, đổi tên lớp, xuất định dạng YOLO

# %%
from fiftyone import ViewField as F

merged = fo.Dataset("proctor-merged", overwrite=True)
for p in parts:
    field = "ground_truth" if p.has_sample_field("ground_truth") else "detections"
    for s in p.iter_samples(progress=True):
        dets = s[field].detections if s[field] else []
        keep = []
        for d in dets:
            lab = OI_MAP.get(d.label, d.label)
            if lab in CLASSES:
                d.label = lab
                keep.append(d)
        merged.add_sample(fo.Sample(filepath=s.filepath, tags=s.tags, gt=fo.Detections(detections=keep)))
print(merged.count_values("gt.detections.label"))

EXPORT = WORK / "export"
for split, tag in (("train", "train"), ("val", "validation")):
    merged.match_tags(tag).export(export_dir=str(EXPORT), dataset_type=fo.types.YOLOv5Dataset, label_field="gt",
                                  split=split, classes=CLASSES)
print((EXPORT / "dataset.yaml").read_text())

# %% [markdown]
# ## 3. Làm mờ mặt + thu nhỏ 320 px + JPEG 0,7 — giống hệt khung thí sinh gửi lên
# Dò mặt bằng YuNet (OpenCV Zoo, MIT). Tọa độ nhãn YOLO là tỉ lệ nên không đổi khi thu nhỏ ảnh.

# %%
import urllib.request

import cv2
import numpy as np
from tqdm.auto import tqdm

YUNET = WORK / "face_detection_yunet_2023mar.onnx"
if not YUNET.exists():
    urllib.request.urlretrieve("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx", YUNET)
detector = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.6)


def pixelate_faces(img: np.ndarray) -> tuple[np.ndarray, int]:
    h, w = img.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(img)
    n = 0
    for f in (faces if faces is not None else []):
        x, y, fw, fh = f[:4]
        cx, cy = x + fw / 2, y + fh / 2
        bw, bh = fw * 1.5, fh * 1.7
        x0, y0 = int(max(0, cx - bw / 2)), int(max(0, cy - bh / 2))
        x1, y1 = int(min(w, cx + bw / 2)), int(min(h, cy + bh / 2))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        roi = img[y0:y1, x0:x1]
        small = cv2.resize(roi, (6, 8), interpolation=cv2.INTER_AREA)
        img[y0:y1, x0:x1] = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        n += 1
    return img, n


faces_total = 0
for p in tqdm(sorted((EXPORT / "images").rglob("*.jpg"))):
    img = cv2.imread(str(p))
    if img is None:
        continue
    img, n = pixelate_faces(img)
    faces_total += n
    s = IMGSZ / max(img.shape[:2])
    if s < 1:
        img = cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 70])
print("Đã làm mờ", faces_total, "khuôn mặt")

# %% [markdown]
# ## 4. Huấn luyện YOLO11n (nhẹ, chạy CPU được trên server khi chưa có GPU)

# %%
from ultralytics import YOLO

model = YOLO("yolo11n.pt")  # khởi tạo từ trọng số COCO
res = model.train(data=str(EXPORT / "dataset.yaml"), imgsz=IMGSZ, epochs=EPOCHS, batch=64, seed=SEED, patience=15,
                  project=str(WORK / "runs"), name="proctor", exist_ok=True, mosaic=1.0, degrees=5, hsv_v=0.5, fliplr=0.5)
best = Path(res.save_dir) / "weights" / "best.pt"
print(best)

# %% [markdown]
# ## 5. Đánh giá theo lớp, và theo độ phân giải thấp (mô phỏng máy yếu / mạng chậm)

# %%
import pandas as pd

m = YOLO(best)
rows = []
for sz in (320, 256, 192):
    met = m.val(data=str(EXPORT / "dataset.yaml"), imgsz=sz, split="val", verbose=False)
    for i, c in enumerate(CLASSES):
        rows.append({"imgsz": sz, "class": c, "AP50": round(float(met.box.ap50[i]), 4), "AP50-95": round(float(met.box.ap[i]), 4)})
table = pd.DataFrame(rows).pivot(index="class", columns="imgsz", values="AP50")
table.to_csv(OUT / "results_yolo.csv")
table

# %% [markdown]
# ## 6. Xuất ONNX cho server ExamGuard
# Batch động (server gom 32–64 khung/lần), ảnh 320 px. Tên lớp được ghi trong metadata `names`.

# %%
onnx_path = Path(m.export(format="onnx", imgsz=IMGSZ, dynamic=True, simplify=True, opset=17))
import onnx
import onnxruntime as ort

mod = onnx.load(onnx_path)
mod.ir_version = min(mod.ir_version, 9)  # tương thích onnxruntime cũ hơn trên server
onnx.save(mod, onnx_path)
sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
print([(i.name, i.shape) for i in sess.get_inputs()], [(o.name, o.shape) for o in sess.get_outputs()])
print(sess.get_modelmeta().custom_metadata_map.get("names"))
final = OUT / "proctor_yolo.onnx"
final.write_bytes(onnx_path.read_bytes())
print("Chép file này vào ExamGuard/backend/models/proctor_yolo.onnx:", final)

# %% [markdown]
# ## 7. Kiểm tra nhanh giống server: xác suất theo nhóm trên một khung đã làm mờ
# Cùng logic với `backend/app/services/vision.py`: điện thoại/sách/tai nghe/màn hình = xác suất lớn nhất;
# "người thứ hai" = độ tin cậy của hộp người thứ HAI sau NMS.

# %%
val_imgs = sorted((EXPORT / "images" / "val").glob("*.jpg"))[:8]
for p in val_imgs:
    r = m.predict(str(p), imgsz=IMGSZ, conf=0.25, verbose=False)[0]
    by = {}
    for c, conf in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist()):
        by.setdefault(CLASSES[int(c)], []).append(round(conf, 2))
    persons = sorted(by.get("person", []), reverse=True)
    print(p.name, {k: max(v) for k, v in by.items() if k != "person"}, "| người thứ hai:", persons[1] if len(persons) > 1 else 0)
