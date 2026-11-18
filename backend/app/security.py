"""Mật khẩu, JWT, phân quyền (quản trị / giám thị / thí sinh) và ghi nhật ký truy cập."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import Attempt, AuditLog, User

ALGO = "HS256"


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 260_000)
    return "pbkdf2$260000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(it))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:  # noqa: BLE001
        return False


def random_code(n: int = 8, alphabet: str = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789") -> str:
    return "".join(secrets.choice(alphabet) for _ in range(n))


def make_token(secret: str, sub: str, role: str, ttl: int, **extra) -> str:
    payload = {"sub": sub, "role": role, "exp": int(time.time()) + ttl, **extra}
    return jwt.encode(payload, secret, algorithm=ALGO)


def decode_token(secret: str, token: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=[ALGO])
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(401, "Phiên đăng nhập đã hết hạn.") from e
    except jwt.PyJWTError as e:
        raise HTTPException(401, "Token không hợp lệ.") from e


def _bearer(authorization: str) -> str:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Chưa đăng nhập.")
    return authorization[7:].strip()


def current_user(request: Request, authorization: str = Header(""), db: Session = Depends(get_db)) -> User:
    claims = decode_token(request.app.state.settings.secret_key, _bearer(authorization))
    if claims.get("role") not in ("admin", "proctor"):
        raise HTTPException(403, "Không có quyền.")
    user = db.get(User, int(claims["sub"]))
    if not user:
        raise HTTPException(401, "Tài khoản không tồn tại.")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Chỉ quản trị viên được thực hiện.")
    return user


def require_staff(user: User = Depends(current_user)) -> User:
    return user


def current_attempt(request: Request, authorization: str = Header(""), db: Session = Depends(get_db)) -> Attempt:
    claims = decode_token(request.app.state.settings.secret_key, _bearer(authorization))
    if claims.get("role") != "candidate":
        raise HTTPException(403, "Không phải phiên thí sinh.")
    att = db.get(Attempt, int(claims["sub"]))
    if not att:
        raise HTTPException(401, "Phiên thi không tồn tại.")
    return att


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else ""


def audit(db: Session, request: Request | None, user: User | None, action: str, target: str = "", detail: str = "") -> None:
    db.add(AuditLog(user_id=user.id if user else None, action=action, target=target, detail=detail[:2000],
                    ip=client_ip(request) if request else ""))
    db.commit()
