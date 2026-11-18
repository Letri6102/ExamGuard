# ExamGuard — nền tảng thi trực tuyến có hỗ trợ phát hiện gian lận

Cài đặt theo `KE_HOACH_TRIEN_KHAI.md`: mọi laptop có webcam đều thi được; máy thí sinh chỉ làm việc nhẹ, phần nặng ở server;
dữ liệu thô (video, âm thanh, nội dung phím) không rời máy. **Hệ thống chỉ hỗ trợ — giám thị là người kết luận.**

```
ExamGuard/
├── shared/feature_schema.json   # "hợp đồng" 54 đặc trưng / 2 giây dùng chung: trình duyệt ↔ server ↔ notebook
├── backend/                     # FastAPI: thi, chấm, pipeline rủi ro, giám thị, thông đồng, truy vết lộ đề
├── frontend/                    # React + Vite: trang thi (giám sát trên máy), quản trị, giám thị
├── notebooks/                   # 4 notebook Google Colab: huấn luyện + đánh giá cho bài báo
├── loadtest/                    # giả lập N thí sinh qua WebSocket (C3)
└── docker-compose.yml           # PostgreSQL/TimescaleDB + Redis Streams + ứng dụng
```

## 1. Chạy trên macOS (phát triển)

Cần Python 3.10/3.11 và Node 20+.

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```
```bash
cd frontend && npm install && npm run build
```
`npm install` tự chép WASM của MediaPipe / Silero VAD vào `frontend/public/` và tải mô hình BlazeFace (phòng thi không phụ thuộc CDN).

Tạo dữ liệu mẫu (kỳ thi `DEMO01`, 12 câu, 5 thí sinh, tài khoản giám thị) rồi chạy server:
```bash
cd backend && .venv/bin/python -m app.seed_demo
```
```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8100
```
Mở http://localhost:8100. Lần chạy đầu tạo tài khoản `admin`, mật khẩu ghi ở `backend/data/admin_password.txt`
(hoặc đặt `ADMIN_PASSWORD` trong `backend/.env`). Khi sửa giao diện, chạy thêm `npm run dev` trong `frontend/` và mở http://localhost:5173.

Camera chỉ chạy trên `https://` hoặc `localhost`; phòng thi thật cần reverse proxy HTTPS.

Kiểm thử:
```bash
cd backend && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q
```
```bash
cd frontend && npm test && npm run typecheck
```

## 2. Nối với FaceReg (Giai đoạn 2)

FaceReg là dịch vụ riêng; ExamGuard gọi server-to-server, `ADMIN_TOKEN` của FaceReg không bao giờ ra trình duyệt.

| Bên | Cấu hình |
|---|---|
| FaceReg `.env` | `CHECKIN_SUCCESS_URL=http://localhost:8100/exam/facereg-callback?ftoken={token}` |
| ExamGuard `backend/.env` | `FACEREG_URL=http://localhost:8000` · `FACEREG_PUBLIC_URL=<URL trình duyệt mở>` · `FACEREG_ADMIN_TOKEN=<ADMIN_TOKEN của FaceReg>` |

Luồng: trang kiểm tra thiết bị → nút "Xác thực khuôn mặt" mở `/checkin` của FaceReg (điền sẵn SBD, mã kỳ thi) → xong chuyển về
ExamGuard kèm `ftoken` → server tra `GET /api/admin/checkins/by-token/{ftoken}` (đúng người, đúng kỳ thi, ACCEPT) → cho bắt đầu.
Giữa giờ thi: trình duyệt gửi **ảnh crop khuôn mặt** mỗi 1–3 phút (ngẫu nhiên) và ngay khi mặt xuất hiện lại sau ≥ 5 giây vắng;
server chuyển sang `POST /api/monitor` của FaceReg, chỉ lưu điểm + trạng thái, ảnh bị bỏ ngay.
Hai thay đổi nhỏ đã thêm vào FaceReg cho việc này: `/api/monitor` trả thêm `score`, và endpoint `by-token`.

Mã thí sinh và mã kỳ thi phải trùng giữa hai hệ thống.

## 3. Luồng xử lý (khớp mục 2 của kế hoạch)

**Trước giờ thi:** đăng nhập (mã kỳ thi + SBD + mã dự thi) → kiểm tra trình duyệt, mạng, camera, micro, màn hình phụ
(`screen.isExtended`, chỉ cảnh báo), toàn màn hình → BlazeFace chạy thử trên máy, đo tốc độ → **xếp hạng máy** low/mid/high →
FaceReg → **sinh đề riêng** (hoán vị câu, hoán vị đáp án, tham số, cách diễn đạt) → làm bài từng câu, watermark động mã riêng.

**Trong giờ thi — máy thí sinh** (`frontend/src/monitor/`), một luồng WebSocket duy nhất:

| Tín hiệu | Cách làm | Rời máy |
|---|---|---|
| Khuôn mặt | BlazeFace 1 fps (1,5 s trên máy yếu), yaw/pitch từ 6 điểm mốc | 11 số / 2 giây |
| Âm thanh | Silero VAD (onnxruntime-web); máy yếu/lỗi → ngưỡng năng lượng | tỉ lệ có tiếng nói, số đoạn, năng lượng |
| Nhịp gõ | dwell/flight, loại phím ảo của bộ gõ (Unikey, EVKey, Telex/VNI của hệ điều hành) | đã chuẩn hóa theo 60 s đầu + lượng tử hóa + nhiễu Laplace |
| Trình duyệt | rời tab, mất focus, thoát toàn màn hình, thu nhỏ cửa sổ, đổi màn hình, cố dán/kéo thả/sao chép, gõ sau khi quay lại | số đếm + sự kiện |
| Bài làm | đổi câu, đổi đáp án, thời gian so với độ khó | số |
| Khung hình | 320 px, **mặt bị pixel hóa trên máy**, mỗi 5–10 s ngẫu nhiên + chùm 3–5 khung khi có sự kiện | JPEG ~10 KB |
| Watchdog | đo độ trễ khung hình giao diện; chậm → giảm tần suất | `lag_ms`, `monitor_load` |

Mất mạng: đặc trưng và câu trả lời đệm trong IndexedDB, gửi bù khi có lại (server bỏ trùng); mất mạng chỉ ghi nhận, không coi là gian lận.
Không có thao tác nào tự khóa bài.

**Trong giờ thi — server** (`backend/app/services/pipeline.py`): gateway WebSocket → hàng đợi tách luồng đặc trưng / khung
(khung 2 mức ưu tiên: chùm sự kiện luôn giữ, khung mẫu bỏ ngẫu nhiên khi quá tải) → YOLO theo lô trên khung mờ mặt (bỏ khung sau khi
xử lý) → bổ sung đặc trưng danh tính + vật thể → **chấm rủi ro** (mô hình hợp nhất ONNX nếu có, không thì luật ngưỡng có giải thích)
→ ghi lô xuống CSDL → cờ (ngưỡng theo hạng máy) → đẩy dashboard qua WebSocket chỉ khi điểm đổi.

**Giám thị** (`/proctor`): bảng trực tiếp; chi tiết phiên với đồ thị rủi ro, lý do cờ, khung bằng chứng (mặt đã mờ; mọi lần xem đều
ghi nhật ký), sự kiện, xác thực lại; **xác nhận/bác cờ = nhãn**; mẫu ngẫu nhiên phiên không bị gắn cờ (ước lượng recall);
thống kê C4/C5 (tỉ lệ cờ đúng, κ giữa giám thị, theo hạng máy, độ trễ pipeline); xuất đặc trưng + nhãn **chỉ có mã giả danh**.

**Sau giờ thi:** thông đồng (chỉ số ω có điều kiện năng lực/độ khó, đáp án quy về đề gốc, ngưỡng Bonferroni, lọc trước bằng đáp án
sai hiếm cho lớp lớn, DTW thời gian, nhóm Louvain); truy vết lộ đề (mã watermark + dấu vân tay nội dung: thứ tự câu, thứ tự
phương án, giá trị tham số, cách diễn đạt).

## 4. Notebook huấn luyện (Google Colab)

Mở https://colab.research.google.com → **File → Upload notebook** → chọn file trong `notebooks/` → Runtime → GPU T4 → Run all.
Mỗi notebook tự cài thư viện, tự tải dữ liệu, nhúng sẵn `feature_schema.json` + `eg_common.py`, lưu kết quả vào
`MyDrive/examguard/`. Chạy theo thứ tự:

| Notebook | Nội dung | Dữ liệu tự tải | Kết quả → nơi dùng |
|---|---|---|---|
| `01_keystroke_llm` | Phát hiện chép câu trả lời LLM bằng nhịp gõ, song ngữ Việt–Anh; XGBoost + TCN; riêng/chung/chéo ngôn ngữ; né tránh (FP/FT) | Dong et al. 2026 (tiếng Việt, ~640 MB, Google Drive), IJCB 2024 (tiếng Anh, Git LFS) | `keystroke_windows.parquet` → notebook 03, 04 |
| `02_yolo_proctor` | YOLO11n trên ảnh đã làm mờ mặt, 320 px: người, điện thoại, sách, laptop, tv, tai nghe | COCO 2017 + Open Images V7 (FiftyOne) | `proctor_yolo.onnx` → `backend/models/` |
| `03_fusion_mil` | Mô hình hợp nhất GRU streaming, MIL, modality dropout, tiền huấn luyện tự giám sát; baseline luật; ablation; ngưỡng theo hạng máy | OEP qua Kaggle (cần `kaggle.json`) + phiên tổng hợp + (tùy chọn) CSV xuất từ ExamGuard | `fusion_gru.onnx` → `backend/models/`; `TIER_THRESHOLDS` → `backend/.env` |
| `04_privacy_federated` | Tái nhận dạng nhịp gõ trước/sau lớp bảo vệ (đường cong privacy–utility), dựng mặt từ khung mờ (LFW + ArcFace), membership inference không DP vs DP-SGD, FedAvg/FedProx/DP-FedAvg | LFW (sklearn), dữ liệu notebook 01 | hình + bảng cho C2 |

Sửa notebook: sửa `notebooks/src/*.py` hoặc `notebooks/eg_common.py` rồi chạy `python3 notebooks/build_notebooks.py`.
Công thức đặc trưng trong `eg_common.py` phải giữ khớp `frontend/src/monitor/features.ts`.

Lưu ý khi viết bài: kiểm tra giấy phép từng bộ dữ liệu (hai repo keystroke không ghi license); OEP yêu cầu trích dẫn bài gốc; phần
dùng phiên tổng hợp phải nêu rõ — kết quả trên dữ liệu tổng hợp **không** phải bằng chứng hiệu quả (các đoạn gian lận tổng hợp được
thiết kế quanh chính các tín hiệu mà luật ngưỡng dùng).

## 5. Triển khai (Docker Compose)

```bash
cp backend/.env.example backend/.env
```
```bash
docker compose up -d --build
```
Ứng dụng dùng PostgreSQL (`DATABASE_URL`) và Redis Streams (`REDIS_URL`) khi được đặt; đặt mô hình vào `backend/models/`.
Kiểm thử tải: `ADMIN_PASSWORD=... python loadtest/simulate_candidates.py -n 1000 --minutes 5`.

## 6. Đối chiếu với kế hoạch

| Giai đoạn | Đã có | Chưa có / cần làm tiếp |
|---|---|---|
| 1 Nền tảng | ngân hàng câu hỏi (1/nhiều đáp án, số có tham số, tự luận), kỳ thi, ca thi, nhập CSV, từng câu, giới hạn giờ mỗi câu, autosave, đệm IndexedDB, phân quyền 3 vai trò, nhật ký xem bằng chứng, mã giả danh | Moodle/LTI 1.3; quản lý ca thi trên giao diện còn tối giản |
| 2 FaceReg | check-in → token → xác minh server-to-server; xác thực lại bằng ảnh crop; điểm đưa vào luồng đặc trưng | chưa thử với FaceReg thật trên trình duyệt (đã thử bằng FaceReg giả lập trong test); load test riêng luồng check-in |
| 3 Trình duyệt | toàn màn hình, rời tab/focus, thu nhỏ, màn hình phụ, chặn dán/kéo thả/sao chép/menu, xử lý IME | kiểm thử thủ công Unikey/EVKey trên Windows |
| 4 Máy thí sinh | đủ các luồng ở mục 3, lượng tử hóa + Laplace, watchdog, hạng máy | đo CPU trên máy yếu thật (DoD ≤ 15–20% một nhân) |
| 5 Server | WebSocket, hàng đợi 2 luồng + 2 mức ưu tiên, YOLO theo lô, ghi lô, dashboard đẩy theo thay đổi, mẫu ngẫu nhiên | Redis Streams mới kiểm thử bằng fakeredis; hypertable TimescaleDB bị bỏ qua vì khóa chính hiện tại không chứa cột thời gian; đường dự phòng GPU→CPU (hiện chạy CPU) |
| 6 Mô hình | notebook 01–04; luật ngưỡng làm baseline; thông đồng ω + DTW + Louvain | hàm gán nhãn yếu + label model, anomaly detection, so sánh CHEESE trên OEP |
| 7 Lộ đề | đề riêng + watermark hiển thị động + truy vết | watermark ẩn (paper 2) |
| 8–11 | notebook 04; thống kê C4/C5 trên trang giám thị; script kiểm thử tải | thí nghiệm thật, triển khai thật |
