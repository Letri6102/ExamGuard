"""Đặt một mật khẩu chung cho mọi tài khoản để thử trên máy (KHÔNG dùng cho kỳ thi thật).

    cd backend && .venv/bin/python -m app.set_test_passwords            # mặc định: test123
    cd backend && .venv/bin/python -m app.set_test_passwords matkhaukhac
    cd backend && .venv/bin/python -m app.set_test_passwords --reset    # xóa luôn các bài làm thử để thi lại từ đầu

- Mọi tài khoản cán bộ (admin, giám thị...) đổi mật khẩu thành giá trị này.
- Mọi thí sinh ở mọi kỳ thi đổi mã dự thi thành giá trị này (lưu chữ in hoa; khi đăng nhập gõ hoa hay thường đều được).
- --reset: xóa mọi lượt làm bài (bài làm, sự kiện, cờ...) để mọi thí sinh thi lại được; sao lưu file DB trước khi xóa.
- Ghi danh sách tài khoản vào TAI_KHOAN_TEST.txt ở thư mục gốc dự án (đã nằm trong .gitignore).
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from sqlalchemy import delete, func, select

from . import db as dbmod
from .config import load_settings
from .models import Attempt, Candidate, Exam, Registration, User
from .security import hash_password

ROOT = Path(__file__).resolve().parents[2]
OUT_FILE = ROOT / "TAI_KHOAN_TEST.txt"


def main(argv: list[str]) -> int:
    reset = "--reset" in argv
    args = [a for a in argv if a != "--reset"]
    pw = args[0] if args else "test123"
    if not 1 <= len(pw) <= 16:
        print("Mật khẩu cần từ 1 đến 16 ký tự (mã dự thi tối đa 16 ký tự).")
        return 1
    s = load_settings()
    dbmod.init_engine(s.database_url)
    dbmod.create_all()
    if reset and s.database_url.startswith("sqlite:///"):
        db_file = Path(s.database_url.removeprefix("sqlite:///"))
        if db_file.exists():
            backup = db_file.with_name(f"{db_file.stem}.backup-{time.strftime('%Y%m%d-%H%M%S')}{db_file.suffix}")
            shutil.copy2(db_file, backup)
            print("Đã sao lưu DB:", backup)
    with dbmod.SessionLocal() as db:
        if reset:
            n = db.scalar(select(func.count(Attempt.id)))
            db.execute(delete(Attempt))  # các bảng con xóa theo (ON DELETE CASCADE)
            db.commit()
            print(f"Đã xóa {n} lượt làm bài: mọi thí sinh thi lại được từ đầu.")
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
        lines += ["", "Mã kỳ thi gõ hoa hay thường đều được. Thí sinh đã nộp bài thì không vào lại được; chạy lệnh trên",
                  "kèm --reset để xóa các bài làm thử và thi lại từ đầu."]
        OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Đã đặt mật khẩu cho {len(users)} cán bộ và mã dự thi cho {len(rows)} thí sinh.")
    print(f"Danh sách tài khoản: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
