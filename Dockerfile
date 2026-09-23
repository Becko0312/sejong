FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SEJONG_DATA=/data
WORKDIR /app
RUN groupadd --gid 10001 studio && useradd --uid 10001 --gid studio --create-home studio \
    && mkdir /data && chown studio:studio /data
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
USER studio

FROM base AS api
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]

FROM base AS worker
USER root
RUN apt-get update && apt-get install --no-install-recommends -y \
    poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-kor tesseract-ocr-mon \
    && rm -rf /var/lib/apt/lists/*
COPY app ./app
USER studio
ENV OMP_THREAD_LIMIT=1
CMD ["python", "-m", "app.worker"]
