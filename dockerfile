 # syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# El Niño-Aware Health Bulletin Parser – production image
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=5000 \
    FLASK_ENV=production

# System libraries:
#  - tesseract-ocr + language packs : pytesseract fallback + Marathi OCR
#  - libgl1/libglib2.0-0/libsm6/libxext6/libxrender1 : OpenCV (RapidOCR)
#  - libgomp1 : onnxruntime threading
#  - poppler-utils : pdfplumber extras
#  - build-essential : wheels that need compilation
#  - curl : for the HEALTHCHECK probe
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-mar \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgomp1 \
        poppler-utils \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python dependencies (cached layer) ------------------------------------
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel \
 && pip install -r requirements.txt \
 && python -m spacy download en_core_web_sm

# --- Application code ------------------------------------------------------
COPY . .

# Writable locations for runtime data
RUN mkdir -p /app/instance /app/uploads

# Drop privileges
RUN useradd --create-home --shell /bin/bash appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# Gunicorn serves the Flask app from wsgi.py
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]