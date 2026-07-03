# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update -qq && apt-get install -y -qq curl && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local

COPY src/ ./src/

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --start-period=8s --retries=3 \
  CMD curl -sf http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "src.rate_limiter.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
