# Builder: resolve and install the package into a throwaway prefix so the
# runtime stage never needs a compiler toolchain or pip's own footprint.
FROM python:3.11-slim AS builder
WORKDIR /app

COPY pyproject.toml ./
COPY ai_platform ./ai_platform
RUN pip install --no-cache-dir --prefix=/install .

# Runtime: slim base, non-root user, only the installed package — no
# source-of-truth files (tests/, engineer-tutorial/) that add nothing at
# runtime.
FROM python:3.11-slim
WORKDIR /app

RUN useradd --create-home --uid 1000 appuser
COPY --from=builder /install /usr/local
COPY ai_platform ./ai_platform

USER appuser
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "ai_platform.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
