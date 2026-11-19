#!/bin/bash
# Nhấn đúp file này trong Finder để khởi động ExamGuard và mở trình duyệt.
# Lần đầu sẽ tự cài môi trường Python và build giao diện (mất vài phút).
# Đóng cửa sổ Terminal hoặc bấm Ctrl+C để tắt server.

PORT="${PORT:-8100}"
URL="http://localhost:$PORT"
ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mLỖI: %s\033[0m\n' "$1"; read -r -p "Nhấn Enter để đóng..."; exit 1; }

cd "$ROOT" || fail "Không vào được thư mục $ROOT"

# Thư mục nằm trong iCloud Drive: kéo các file bị đẩy lên mây về máy để server không bị treo khi đọc.
if command -v brctl >/dev/null 2>&1; then
  brctl download "$ROOT" >/dev/null 2>&1 || true
fi

# Server đã chạy sẵn thì chỉ mở trình duyệt.
if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
  say "ExamGuard đang chạy sẵn ở $URL"
  open "$URL"
  exit 0
fi
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Cổng $PORT đang bị chương trình khác dùng. Tắt chương trình đó hoặc chạy: PORT=8200 ./ExamGuard.command"
fi

# 1. Môi trường Python (đặt ngoài iCloud, nối vào backend/.venv).
if [ ! -x "$BACKEND/.venv/bin/uvicorn" ]; then
  say "Cài môi trường Python (chỉ lần đầu)"
  PY=""
  for c in python3.11 python3.10 python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$(command -v "$c")"; break; fi
  done
  [ -n "$PY" ] || fail "Không tìm thấy Python 3. Cài bằng: brew install python@3.11"
  VENV="$HOME/.venvs/examguard-backend"
  [ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV" || fail "Tạo venv thất bại"
  [ -e "$BACKEND/.venv" ] || ln -s "$VENV" "$BACKEND/.venv"
  "$BACKEND/.venv/bin/pip" install -q --upgrade pip
  "$BACKEND/.venv/bin/pip" install -r "$BACKEND/requirements.txt" || fail "Cài thư viện Python thất bại"
fi

# 2. Giao diện: build khi chưa có hoặc mã nguồn mới hơn bản build.
DIST_INDEX="$FRONTEND/dist/index.html"
NEED_BUILD=0
if [ ! -f "$DIST_INDEX" ]; then
  NEED_BUILD=1
elif [ -n "$(find "$FRONTEND/src" "$FRONTEND/index.html" "$FRONTEND/package.json" -newer "$DIST_INDEX" -print -quit 2>/dev/null)" ]; then
  NEED_BUILD=1
fi
if [ "$NEED_BUILD" = 1 ]; then
  command -v npm >/dev/null 2>&1 || fail "Không tìm thấy npm. Cài bằng: brew install node"
  cd "$FRONTEND" || fail "Không vào được $FRONTEND"
  if [ ! -d node_modules ]; then
    say "Cài thư viện giao diện (chỉ lần đầu)"
    # node_modules để ngoài đồng bộ iCloud (đuôi .nosync) rồi nối vào.
    mkdir -p node_modules.nosync && ln -s node_modules.nosync node_modules
    npm install || fail "npm install thất bại"
  fi
  say "Build giao diện"
  npm run build || fail "Build giao diện thất bại"
  cd "$ROOT" || exit 1
fi

# 3. Chạy server.
say "Khởi động ExamGuard ở $URL"
cd "$BACKEND" || fail "Không vào được $BACKEND"
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" &
SERVER=$!
trap 'echo; say "Đang tắt server"; kill $SERVER 2>/dev/null; wait $SERVER 2>/dev/null; exit 0' INT TERM HUP

for _ in $(seq 1 60); do
  if curl -fsS "$URL/api/health" >/dev/null 2>&1; then break; fi
  kill -0 $SERVER 2>/dev/null || fail "Server dừng khi khởi động, xem lỗi ở trên"
  sleep 0.5
done

DATA_DIR="$(grep -E '^DATA_DIR=' "$BACKEND/.env" 2>/dev/null | tail -1 | cut -d= -f2-)"
DATA_DIR="${DATA_DIR:-$BACKEND/data}"
say "Đã sẵn sàng: $URL"
echo "   Tài khoản quản trị / demo: xem $DATA_DIR/admin_password.txt và demo_accounts.txt"
echo "   Đóng cửa sổ này hoặc bấm Ctrl+C để tắt server."
open "$URL"

wait $SERVER
