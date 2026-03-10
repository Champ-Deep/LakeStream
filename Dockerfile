FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install "scrapling[fetchers]" \
    && python -m playwright install-deps \
    && scrapling install

COPY src/ ./src/
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV PORT=8080
ENV SERVICE_MODE=api
EXPOSE ${PORT}

CMD ["./entrypoint.sh"]
