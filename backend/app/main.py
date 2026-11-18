"""ExamGuard API. Chạy: uvicorn app.main:app --port 8100 (trong thư mục backend/)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import db as dbmod
from .config import ROOT_DIR, Settings, load_settings
from .models import User
from .routers import admin, analysis, auth, exam, proctor
from .security import hash_password, random_code
from .services.bus import Hub, InMemoryBus, RedisBus
from .services.facereg import FaceRegClient
from .services.pipeline import Pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("examguard")
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


def ensure_admin(s: Settings) -> None:
    with dbmod.SessionLocal() as db:
        if db.scalar(select(User.id).where(User.role == "admin").limit(1)):
            return
        if db.scalar(select(User.id).where(User.username == s.admin_username)):
            log.warning("Đã có tài khoản '%s' nhưng không phải quản trị; đặt ADMIN_USERNAME khác.", s.admin_username)
            return
        pw = s.admin_password or random_code(14, "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789")
        db.add(User(username=s.admin_username, password_hash=hash_password(pw), role="admin", full_name="Quản trị"))
        db.commit()
        if not s.admin_password:
            f = s.data_dir / "admin_password.txt"
            f.write_text(f"{s.admin_username}\n{pw}\n")
            f.chmod(0o600)
            log.warning("Đã tạo tài khoản quản trị '%s'. Mật khẩu lưu tại %s (đổi/xóa sau khi đăng nhập).", s.admin_username, f)


def create_app(settings: Settings | None = None, bus=None) -> FastAPI:
    s = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        dbmod.init_engine(s.database_url)
        dbmod.create_all()
        ensure_admin(s)
        the_bus = bus or (RedisBus(s.redis_url, s.frame_queue_max) if s.redis_url else InMemoryBus(s.frame_queue_max))
        app.state.pipeline = Pipeline(s, the_bus, Hub())
        app.state.pipeline.start()
        app.state.facereg = FaceRegClient(s)
        log.info("Hàng đợi: %s | chấm rủi ro: %s | YOLO: %s | FaceReg: %s",
                 the_bus.kind, app.state.pipeline.scorer.name, app.state.pipeline.detector is not None,
                 s.facereg_url or "TẮT (chế độ thử)")
        yield
        await app.state.pipeline.stop()
        await app.state.facereg.close()

    app = FastAPI(title="ExamGuard", version="0.1.0", lifespan=lifespan)
    app.state.settings = s
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    for r in (auth.router, admin.router, exam.router, proctor.router, analysis.router, exam.ws_router, proctor.ws_router):
        app.include_router(r)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/schema")
    def schema():
        return s.schema

    if FRONTEND_DIST.exists():  # bản build của frontend (npm run build)
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "ws/")):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            f = FRONTEND_DIST / path
            if path and f.is_file() and FRONTEND_DIST in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
