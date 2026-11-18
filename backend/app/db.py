"""Kết nối CSDL (SQLite khi phát triển, PostgreSQL + TimescaleDB khi triển khai)."""
from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger("examguard.db")


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal: sessionmaker | None = None


def init_engine(url: str):
    global engine, SessionLocal
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
    engine = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragma(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine


def create_all() -> None:
    from . import models  # noqa: F401  (đăng ký bảng)

    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        # Chuỗi đặc trưng theo thời gian: hypertable nếu có TimescaleDB (bỏ qua nếu không có)
        try:
            with engine.begin() as c:
                c.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
                c.execute(text("SELECT create_hypertable('feature_windows', 'created_at', "
                               "if_not_exists => TRUE, migrate_data => TRUE)"))
            log.info("feature_windows là hypertable TimescaleDB")
        except Exception as e:  # noqa: BLE001
            log.info("Không dùng TimescaleDB (%s)", str(e).splitlines()[0])


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
