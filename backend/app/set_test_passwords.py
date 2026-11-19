"""Đặt một mật khẩu chung cho mọi tài khoản để thử trên máy (KHÔNG dùng cho kỳ thi thật).

    cd backend && .venv/bin/python -m app.set_test_passwords            # mặc định: test123
    cd backend && .venv/bin/python -m app.set_test_passwords matkhaukhac

- Mọi tài khoản cán bộ (admin, giám thị...) đổi mật khẩu thành giá trị này.
- Mọi thí sinh ở mọi kỳ thi đổi mã dự thi thành giá trị này (lưu chữ in hoa; khi đăng nhập gõ hoa hay thường đều được).
- Ghi danh sách tài khoản vào TAI_KHOAN_TEST.txt ở thư mục gốc dự án (đã nằm trong .gitignore).
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

from . import db as dbmod
from .config import load_settings
from .models import Candidate, Exam, Registration, User
from .security import hash_password

ROOT = Path(__file__).resolve().parents[2]
OUT_FILE = ROOT / "TAI_KHOAN_TEST.txt"


def main(argv: list[str]) -> int:
    pw = argv[0] if argv else "test123"
    if not 1 <= len(pw) <= 16:
        print("Mật khẩu cần từ 1 đến 16 ký tự (mã dự thi tối đa 16 ký tự).")
        return 1
    s = load_settings()
    dbmod.init_engine(s.database_url)
    dbmod.create_all()
    with dbmod.SessionLocal() as db:
        users = db.scalars(select(User).order_by(User.role, User.username)).all()
        for u in users:
            u.password_hash = hash_password(pw)
        rows = db.execute(select(Registration, Exam.code, Candidate.code, Candidate.full_name)
                          .join(Exam, Exam.id == Registration.exam_id)
                          .join(Candidate, Candidate.id == Registration.candidate_id)
                          .order_by(Exam.code, Candidate.code)).all()
        for reg, *_ in rows:
            reg.access_code = pw.upper()
        db.commit()

        lines = [
            "TÀI KHOẢN THỬ TRÊN MÁY — chỉ để kiểm tra, không dùng cho kỳ thi thật. File này không được commit.",
            f"Tạo lại: cd backend && .venv/bin/python -m app.set_test_passwords {pw}",
            "",
            "Web: http://localhost:8100",
            "",
            "== Cán bộ: trang đăng nhập cán bộ (tên đăng nhập / mật khẩu) ==",
        ]
        lines += [f"  {u.username:<12} {pw:<10} ({u.role})" for u in users]
        lines += ["", "== Thí sinh: trang thi (mã kỳ thi / số báo danh / mã dự thi) =="]
        lines += [f"  {exam} / {cand} / {pw}   ({name})" for _, exam, cand, name in rows]
        lines += ["", "Thí sinh đã nộp bài thì không đăng nhập lại được; dùng thí sinh khác hoặc tạo kỳ thi mới ở trang quản trị."]
        OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Đã đặt mật khẩu cho {len(users)} cán bộ và mã dự thi cho {len(rows)} thí sinh.")
    print(f"Danh sách tài khoản: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
