# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BESTNAI_HOST=0.0.0.0 \
    BESTNAI_PORT=8010 \
    BESTNAI_REQUIRE_AUTH=true \
    BESTNAI_DEFAULT_MAX_ANLAS=0 \
    BESTNAI_QUEUE_TIMEOUT=30 \
    BESTNAI_SDK_SRC=/app/novelai-sdk/src

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY app ./app
COPY run.py ./
COPY vendor/novelai-sdk ./novelai-sdk

EXPOSE 8010

CMD ["python", "run.py"]
