"""Tạo dữ liệu mẫu: python -m app.seed_demo  (trong thư mục backend/)

Tạo kỳ thi DEMO01 (12 câu: trắc nghiệm 1/nhiều đáp án, câu số có tham số, tự luận, nhiều cách diễn đạt),
5 thí sinh SV001..SV005 và tài khoản giám thị. In ra mã dự thi để đăng nhập.
"""
from __future__ import annotations

import json
import sys

from sqlalchemy import select

from . import db as dbmod
from .config import load_settings
from .models import Candidate, Exam, ExamQuestion, Question, Registration, User
from .security import hash_password, random_code

QUESTIONS = [
    dict(qtype="single", stem="Độ phức tạp thời gian của tìm kiếm nhị phân trên mảng n phần tử đã sắp xếp là?",
         variants=["Tìm kiếm nhị phân trên mảng đã sắp xếp có độ phức tạp thời gian bao nhiêu?"],
         options=["O(log n)", "O(n)", "O(n log n)", "O(1)"], answer=["A"], difficulty=-0.5, expected_seconds=40),
    dict(qtype="single", stem="Giao thức nào hoạt động ở tầng giao vận (transport) trong mô hình TCP/IP?",
         variants=["Trong mô hình TCP/IP, giao thức nào thuộc tầng giao vận?"],
         options=["UDP", "IP", "HTTP", "ARP"], answer=["A"], difficulty=0.0, expected_seconds=40),
    dict(qtype="multi", stem="Những cấu trúc dữ liệu nào hoạt động theo nguyên tắc FIFO hoặc LIFO?",
         options=["Ngăn xếp (stack)", "Hàng đợi (queue)", "Bảng băm", "Cây nhị phân tìm kiếm"], answer=["A", "B"],
         difficulty=0.3, expected_seconds=60),
    dict(qtype="numeric", stem="Một mảng có {{n}} phần tử. Thuật toán sắp xếp nổi bọt thực hiện tối đa bao nhiêu phép so sánh?",
         variants=["Sắp xếp nổi bọt trên {{n}} phần tử cần tối đa bao nhiêu phép so sánh?"],
         params={"n": {"min": 5, "max": 30}}, answer="n*(n-1)/2", difficulty=0.5, expected_seconds=90),
    dict(qtype="single", stem="Tính {{a}} + {{b}} × 2.",
         params={"a": {"min": 3, "max": 40}, "b": {"min": 2, "max": 25}},
         options=["{{a + b*2}}", "{{(a+b)*2}}", "{{a*b + 2}}", "{{a + b + 2}}"], answer=["A"], difficulty=-1.0,
         expected_seconds=30),
    dict(qtype="single", stem="Câu lệnh SQL nào dùng để xóa toàn bộ dữ liệu của bảng nhưng giữ cấu trúc bảng?",
         options=["TRUNCATE TABLE", "DROP TABLE", "DELETE DATABASE", "ALTER TABLE"], answer=["A"], difficulty=0.2,
         expected_seconds=40),
    dict(qtype="numeric", stem="Một cây nhị phân đầy đủ có chiều cao {{h}} (gốc ở độ cao 0). Số nút tối đa là bao nhiêu?",
         params={"h": {"min": 2, "max": 9}}, answer="2**(h+1) - 1", difficulty=0.6, expected_seconds=90),
    dict(qtype="single", stem="Trong Git, lệnh nào tạo một nhánh mới và chuyển sang nhánh đó?",
         variants=["Lệnh Git nào vừa tạo nhánh mới vừa chuyển sang nhánh đó?"],
         options=["git switch -c <tên>", "git merge <tên>", "git fetch <tên>", "git stash <tên>"], answer=["A"],
         difficulty=0.1, expected_seconds=40),
    dict(qtype="multi", stem="Những biện pháp nào giúp chống tấn công SQL injection?",
         options=["Câu lệnh tham số hóa", "Kiểm tra/lọc dữ liệu đầu vào", "Tắt HTTPS", "Ghép chuỗi trực tiếp vào câu SQL"],
         answer=["A", "B"], difficulty=0.4, expected_seconds=60),
    dict(qtype="single", stem="Bộ nhớ nào có tốc độ truy cập nhanh nhất?",
         options=["Thanh ghi CPU", "Bộ nhớ đệm L3", "RAM", "Ổ SSD"], answer=["A"], difficulty=-0.8, expected_seconds=30),
    dict(qtype="numeric", stem="Mạng /{{p}} (IPv4) có bao nhiêu địa chỉ host dùng được?",
         params={"p": {"min": 22, "max": 29}}, answer="2**(32-p) - 2", difficulty=0.8, expected_seconds=120),
    dict(qtype="essay", stem="Trình bày ngắn gọn (5–8 câu) sự khác nhau giữa tiến trình (process) và luồng (thread).",
         variants=["Hãy so sánh tiến trình và luồng trong hệ điều hành (5–8 câu)."], difficulty=0.5,
         expected_seconds=420),
]


def main() -> int:
    s = load_settings()
    dbmod.init_engine(s.database_url)
    dbmod.create_all()
    with dbmod.SessionLocal() as db:
        if db.scalar(select(Exam.id).where(Exam.code == "DEMO01")):
            print("Đã có kỳ thi DEMO01.")
        else:
            qs = []
            for d in QUESTIONS:
                opts = [{"id": chr(65 + i), "text": t} for i, t in enumerate(d.get("options", []))]
                q = Question(bank="demo", qtype=d["qtype"], stem=d["stem"],
                             variants_json=json.dumps(d.get("variants", []), ensure_ascii=False),
                             options_json=json.dumps(opts, ensure_ascii=False),
                             answer_json=json.dumps(d.get("answer"), ensure_ascii=False),
                             params_json=json.dumps(d.get("params", {})), difficulty=d["difficulty"],
                             expected_seconds=d["expected_seconds"])
                db.add(q)
                qs.append(q)
            db.flush()
            exam = Exam(code="DEMO01", title="Kiểm tra thử – Nhập môn CNTT", duration_minutes=30,
                        require_facereg=bool(s.facereg_url), status="open")
            db.add(exam)
            db.flush()
            for i, q in enumerate(qs):
                db.add(ExamQuestion(exam_id=exam.id, question_id=q.id, position=i))
            for i in range(1, 6):
                c = db.scalar(select(Candidate).where(Candidate.code == f"SV00{i}")) or Candidate(code=f"SV00{i}", full_name=f"Thí sinh {i}")
                db.add(c)
                db.flush()
                db.add(Registration(exam_id=exam.id, candidate_id=c.id, access_code=random_code(6)))
            db.commit()
        if not db.scalar(select(User.id).where(User.username == "giamthi")):
            pw = random_code(10, "abcdefghijkmnpqrstuvwxyz23456789")
            db.add(User(username="giamthi", password_hash=hash_password(pw), role="proctor", full_name="Giám thị demo"))
            db.commit()
            print(f"Tài khoản giám thị: giamthi / {pw}")
        exam = db.scalar(select(Exam).where(Exam.code == "DEMO01"))
        print(f"\nKỳ thi {exam.code}: {exam.title} | bắt buộc FaceReg: {exam.require_facereg}")
        rows = db.execute(select(Candidate.code, Registration.access_code).join(Registration)
                          .where(Registration.exam_id == exam.id).order_by(Candidate.code)).all()
        for code, ac in rows:
            print(f"  {code}  mã dự thi: {ac}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
