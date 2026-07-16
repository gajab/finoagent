# ---- Stage 1: Build Frontend ----
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --quiet
COPY frontend/ ./
RUN npx vite build

# ---- Stage 2: Build Python Dependencies ----
FROM python:3.12-slim AS python-build
WORKDIR /build
COPY backend/requirements.txt .
# Install to a specific prefix so we can copy it easily
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 3: Final Production Image ----
FROM python:3.12-slim
WORKDIR /app

# Copy ONLY the installed python packages from Stage 2
COPY --from=python-build /install /usr/local
# Copy built frontend from Stage 1
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
# Copy backend code
COPY backend/ ./backend/

RUN mkdir -p /app/data
ENV DATABASE_URL=sqlite+aiosqlite:///./data/stock_research.db
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Remove __pycache__ and other unnecessary files to save those last few MBs
RUN find /usr/local -name "*.pyc" -delete && \
    find /usr/local -name "__pycache__" -delete

# Run migrations then start the server.
# alembic upgrade head is idempotent — safe to run on every boot.
CMD ["sh", "-c", "cd /app/backend && ALEMBIC_ALLOW_PROD=1 alembic upgrade head && cd /app && python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"]