# Bản build gồm frontend (React) + backend (FastAPI) trong một image.
FROM node:22-bookworm-slim AS web
WORKDIR /app
COPY shared ./shared
COPY frontend/package.json frontend/package-lock.json frontend/
COPY frontend/scripts frontend/scripts
RUN cd frontend && npm ci --no-audit --no-fund
COPY frontend frontend
RUN cd frontend && npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt backend/
RUN pip install --no-cache-dir -r backend/requirements.txt "psycopg[binary]>=3.2" "redis>=5"
COPY shared shared
COPY backend backend
COPY --from=web /app/frontend/dist frontend/dist
WORKDIR /app/backend
EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--proxy-headers", "--forwarded-allow-ips", "*"]
