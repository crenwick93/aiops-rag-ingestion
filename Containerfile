FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install "llama-stack-client==0.2.23"

COPY ingest_conf.py .

# PoC entrypoint: ingest Confluence into Llama Stack
ENTRYPOINT ["python", "/app/ingest_conf.py"]

