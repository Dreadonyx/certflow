FROM python:3.12-slim

# Install fonts only — no dev tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (layer cache optimization)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source (respects .dockerignore)
COPY . .

# Create runtime directories and set permissions
RUN mkdir -p uploads output_certs \
    && addgroup --system certflow \
    && adduser --system --ingroup certflow certflow \
    && chown -R certflow:certflow /app

USER certflow

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

CMD ["gunicorn", \
     "--workers", "2", \
     "--bind", "0.0.0.0:5000", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "app:app"]
