"""YOLO (ONNX, xuất bằng Ultralytics) trên khung hình ĐÃ LÀM MỜ MẶT gửi từ máy thí sinh.

Chỉ trả về xác suất theo nhóm (điện thoại, sách, tai nghe, người thứ hai, màn hình phụ). Khung hình được xử lý
xong là bỏ (trừ khi giữ làm bằng chứng cho cờ, xem pipeline). Mô hình huấn luyện ở notebooks/02_yolo_proctor.ipynb;
mô hình COCO gốc (yolo11n.onnx) cũng dùng được cho điện thoại/sách/người/laptop.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("examguard.vision")


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float = 0.5) -> list[int]:
    order = np.argsort(-scores)
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])  # noqa: E731
        ovr = inter / (area(boxes[i]) + area(boxes[order[1:]]) - inter + 1e-9)
        order = order[1:][ovr < iou]
    return keep


class YoloDetector:
    def __init__(self, path: Path, groups: dict[str, list[str]]):
        import onnxruntime as ort

        self.sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        inp = self.sess.get_inputs()[0]
        self.input_name = inp.name
        meta = self.sess.get_modelmeta().custom_metadata_map
        names = ast.literal_eval(meta["names"]) if "names" in meta else {}
        self.names = {int(k): str(v).lower() for k, v in names.items()}
        size = inp.shape[2] if isinstance(inp.shape[2], int) else int(ast.literal_eval(meta.get("imgsz", "[640]"))[0])
        self.size = int(size)
        self.dynamic_batch = not isinstance(inp.shape[0], int)
        self.group_ids: dict[str, list[int]] = {}
        for g, labels in groups.items():
            ids = [i for i, n in self.names.items() if n in labels]
            if ids:
                self.group_ids[g] = ids
        self.person_ids = [i for i, n in self.names.items() if n == "person"]
        log.info("YOLO %s: %d lớp, ảnh %d, nhóm dùng được: %s", path.name, len(self.names), self.size, list(self.group_ids))

    def _letterbox(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        s = self.size / max(h, w)
        nh, nw = int(round(h * s)), int(round(w * s))
        canvas = np.full((self.size, self.size, 3), 114, np.uint8)
        top, left = (self.size - nh) // 2, (self.size - nw) // 2
        canvas[top:top + nh, left:left + nw] = cv2.resize(img, (nw, nh))
        return canvas

    def predict(self, jpegs: list[bytes]) -> list[dict[str, float] | None]:
        imgs, ok = [], []
        for data in jpegs:
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            ok.append(img is not None)
            if img is not None:
                imgs.append(self._letterbox(img))
        if not imgs:
            return [None] * len(jpegs)
        x = np.stack(imgs)[..., ::-1].transpose(0, 3, 1, 2).astype(np.float32) / 255.0
        if self.dynamic_batch:
            out = self.sess.run(None, {self.input_name: x})[0]
        else:
            out = np.concatenate([self.sess.run(None, {self.input_name: x[i:i + 1]})[0] for i in range(len(x))])
        results = [self._summarize(o) for o in out]  # o: [4 + nc, N]
        it = iter(results)
        return [next(it) if good else None for good in ok]

    def _summarize(self, o: np.ndarray) -> dict[str, float]:
        o = np.asarray(o)
        rows = 4 + len(self.names) if self.names else None
        if (rows and o.shape[0] != rows and o.shape[1] == rows) or (not rows and o.shape[0] > o.shape[1]):
            o = o.T  # một số bản xuất ONNX để [N, 4+nc]
        boxes_xywh, cls = o[:4].T, o[4:]
        res = {}
        for g, ids in self.group_ids.items():
            if g == "obj_person_extra":
                continue
            res[g] = float(cls[ids].max()) if cls.size else 0.0
        if self.person_ids:
            sc = cls[self.person_ids].max(axis=0)
            m = sc > 0.25
            if m.sum() >= 2:
                b = boxes_xywh[m]
                xyxy = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], 1)
                keep = nms(xyxy, sc[m])
                confs = sorted((float(sc[m][k]) for k in keep), reverse=True)
                res["obj_person_extra"] = confs[1] if len(confs) > 1 else 0.0  # người thứ HAI
            else:
                res["obj_person_extra"] = 0.0
        return res


def load_detector(path: Path, groups: dict[str, list[str]]) -> YoloDetector | None:
    if not path or not Path(path).exists():
        log.warning("Chưa có mô hình YOLO (%s): bỏ qua phân tích vật thể. Xem notebooks/02_yolo_proctor.ipynb", path)
        return None
    try:
        return YoloDetector(Path(path), groups)
    except Exception as e:  # noqa: BLE001
        log.warning("Không nạp được YOLO (%s)", e)
        return None
