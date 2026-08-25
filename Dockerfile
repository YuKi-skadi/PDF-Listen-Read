FROM python:3.11-slim

WORKDIR /app

# Application code stays in the image; all mutable library data is mounted at /data.
ENV PYTHONUNBUFFERED=1 \
    PDF_LISTEN_DATA_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir pydub dashscope

COPY . .

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
