"""Cấu hình đọc từ biến môi trường hoặc file backend/.env."""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
SCHEMA_PATH = ROOT_DIR / "shared" / "feature_schema.json"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.split(" #", 1)[0].strip().strip('"').strip("'"))


def _env(name: str, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    if cast is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return cast(raw.strip())


@dataclass
class Settings:
    database_url: str
    secret_key: str
    admin_username: str
    admin_password: str
    data_dir: Path
    cors_origins: list[str]
    public_url: str                 # URL trang thi (dùng cho callback FaceReg)

    # FaceReg (dịch vụ xác thực khuôn mặt riêng)
    facereg_url: str                # trống = chế độ thử nghiệm, bỏ qua xác thực khuôn mặt
    facereg_public_url: str         # URL FaceReg mà TRÌNH DUYỆT thí sinh mở (có thể khác URL nội bộ)
    facereg_admin_token: str
    facereg_required: bool

    # Hàng đợi / bus
    redis_url: str                  # trống = hàng đợi trong tiến trình

    # Mô hình
    yolo_model: Path
    yolo_classes: dict[str, list[str]]  # nhóm đặc trưng -> tên lớp của mô hình
    fusion_model: Path
    yolo_batch: int
    frame_queue_max: int

    # Rủi ro & giám thị
    flag_threshold: float           # điểm rủi ro (0..100) để gắn cờ
    tier_thresholds: dict[str, float]
    review_sample_rate: float       # tỉ lệ phiên KHÔNG bị gắn cờ được đưa vào mẫu rà soát ngẫu nhiên
    evidence_frames: str            # none | flagged: giữ khung mờ mặt của chùm sự kiện khi có cờ
    evidence_retention_days: int
    reverify_min_seconds: int
    reverify_max_seconds: int

    # Cảnh báo tức thời cho thí sinh
    candidate_warnings: bool
    warn_on_flag: bool
    warning_cooldown_seconds: int

    schema: dict = field(default_factory=dict)

    @property
    def feature_names(self) -> list[str]:
        return [f["name"] for f in self.schema["features"]]

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"


DEFAULT_YOLO_CLASSES = {
    "obj_phone": ["cell phone", "phone", "mobile"],
    "obj_book": ["book", "paper", "notebook"],
    "obj_headphone": ["headphone", "headphones", "earphone", "earbuds", "headset"],
    "obj_person_extra": ["person"],
    "obj_screen": ["laptop", "tv", "monitor", "screen", "tablet"],
}


def load_settings() -> Settings:
    _load_dotenv(BACKEND_DIR / ".env")
    data_dir = Path(_env("DATA_DIR", str(BACKEND_DIR / "data")))
    if not data_dir.is_absolute():
        data_dir = BACKEND_DIR / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    def path(name: str, default: str) -> Path:
        p = Path(_env(name, default))
        return p if p.is_absolute() else BACKEND_DIR / p

    secret = _env("SECRET_KEY", "")
    if not secret:  # tạo một lần, lưu cạnh dữ liệu để token không mất hiệu lực khi khởi động lại
        sk = data_dir / ".secret_key"
        if not sk.exists():
            sk.write_text(secrets.token_urlsafe(48))
        secret = sk.read_text().strip()

    tiers = {"low": 70.0, "mid": 60.0, "high": 60.0}
    for item in _env("TIER_THRESHOLDS", "").split(","):
        if ":" in item:
            k, v = item.split(":", 1)
            tiers[k.strip()] = float(v)

    yolo_classes = dict(DEFAULT_YOLO_CLASSES)
    raw = _env("YOLO_CLASSES", "")
    if raw:
        yolo_classes.update(json.loads(raw))

    s = Settings(
        database_url=_env("DATABASE_URL", f"sqlite:///{data_dir / 'examguard.db'}"),
        secret_key=secret,
        admin_username=_env("ADMIN_USERNAME", "admin"),
        admin_password=_env("ADMIN_PASSWORD", ""),
        data_dir=data_dir,
        cors_origins=[o.strip() for o in _env("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()],
        public_url=_env("PUBLIC_URL", "http://localhost:5173").rstrip("/"),
        facereg_url=_env("FACEREG_URL", "").rstrip("/"),
        facereg_public_url=_env("FACEREG_PUBLIC_URL", _env("FACEREG_URL", "")).rstrip("/"),
        facereg_admin_token=_env("FACEREG_ADMIN_TOKEN", ""),
        facereg_required=_env("FACEREG_REQUIRED", True, bool),
        redis_url=_env("REDIS_URL", ""),
        yolo_model=path("YOLO_MODEL", "models/proctor_yolo.onnx"),
        yolo_classes=yolo_classes,
        fusion_model=path("FUSION_MODEL", "models/fusion_gru.onnx"),
        yolo_batch=_env("YOLO_BATCH", 32, int),
        frame_queue_max=_env("FRAME_QUEUE_MAX", 2000, int),
        flag_threshold=_env("FLAG_THRESHOLD", 60.0, float),
        tier_thresholds=tiers,
        review_sample_rate=_env("REVIEW_SAMPLE_RATE", 0.05, float),
        evidence_frames=_env("EVIDENCE_FRAMES", "flagged"),
        evidence_retention_days=_env("EVIDENCE_RETENTION_DAYS", 30, int),
        reverify_min_seconds=_env("REVERIFY_MIN_SECONDS", 60, int),
        reverify_max_seconds=_env("REVERIFY_MAX_SECONDS", 180, int),
        candidate_warnings=_env("CANDIDATE_WARNINGS", True, bool),
        warn_on_flag=_env("WARN_ON_FLAG", True, bool),
        warning_cooldown_seconds=_env("WARNING_COOLDOWN_SECONDS", 30, int),
        schema=json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
    )
    s.evidence_dir.mkdir(parents=True, exist_ok=True)
    return s
