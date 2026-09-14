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

# jemalloc — returns freed heap to the OS FAR more aggressively than glibc malloc (which hoards it as
# fragmentation). ~20-40% lower RSS for this numpy/pandas workload with zero code change — the single
# biggest memory win on the 512 MiB instance. Preloaded + tuned via ENV below. (Installed before the
# COPYs so this apt layer caches independently of code changes.)
RUN apt-get update && apt-get install -y --no-install-recommends libjemalloc2 \
    && rm -rf /var/lib/apt/lists/* \
    && test -f /usr/lib/x86_64-linux-gnu/libjemalloc.so.2   # fail the build if the LD_PRELOAD path is wrong

# Copy ONLY the installed python packages from Stage 2
COPY --from=python-build /install /usr/local
# Copy built frontend from Stage 1
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
# Copy backend code
COPY backend/ ./backend/

RUN mkdir -p /app/data
ENV DATABASE_URL=sqlite+aiosqlite:///./data/stock_research.db
ENV PYTHONUNBUFFERED=1

# ---- Memory-footprint tuning for the 512 MiB Cloud Run free tier ----
# Preload jemalloc and tell it to return dirty/muzzy pages to the OS within ~1s of being freed
# (dirty_decay_ms / muzzy_decay_ms) with a small fixed arena count. This is what actually keeps RSS
# down on a numpy/pandas app. Under jemalloc the glibc MALLOC_ARENA_MAX / MALLOC_TRIM_THRESHOLD_ below
# are inert (and _release_memory()'s malloc_trim becomes a no-op) — kept only as a fallback if the
# preload ever fails to load.
ENV LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2
ENV MALLOC_CONF=background_thread:true,narenas:2,dirty_decay_ms:1000,muzzy_decay_ms:1000
# numpy/scipy/pandas link OpenBLAS, which spins up a per-core thread pool with
# scratch buffers. Cloud Run free tier is 1 vCPU, so those extra threads only
# waste RSS (and thrash the single core). Pin every math backend to one thread.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1
# glibc opens up to 8×CPU malloc arenas that hold onto freed memory (fragmentation
# never returned to the OS). Cap the arenas and trim freed blocks back sooner —
# the single biggest RSS win for a numpy-heavy process. See the memory-limit docs.
ENV MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=65536
# matplotlib/mplfinance are lazy-imported for PNG export; force the headless
# backend so they never try to load a GUI toolkit when they do come in.
ENV MPLBACKEND=Agg

EXPOSE 8000

# Remove __pycache__ and other unnecessary files to save those last few MBs
RUN find /usr/local -name "*.pyc" -delete && \
    find /usr/local -name "__pycache__" -delete

# Run migrations then start the server.
# alembic upgrade head is idempotent — safe to run on every boot.
CMD ["sh", "-c", "cd /app/backend && ALEMBIC_ALLOW_PROD=1 alembic upgrade head && cd /app && python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"]