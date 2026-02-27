FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir gunicorn \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install "scrapling[fetchers]" \
    && scrapling install

COPY src/ ./src/

ENV PORT=8000
EXPOSE ${PORT}

CMD ["gunicorn", "src.server:app", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:${PORT}", "--timeout", "120"]