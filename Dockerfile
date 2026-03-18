FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Lightpanda CDP browser (Tier 1 — lightweight headless browser)
RUN curl -fsSL https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux \
    -o /usr/local/bin/lightpanda \
    && chmod +x /usr/local/bin/lightpanda

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install "scrapling[fetchers]" \
    && python -m playwright install-deps \
    && scrapling install

COPY src/ ./src/
COPY entrypoint.sh start.sh ./
RUN chmod +x entrypoint.sh start.sh

ENV PORT=8080
ENV SERVICE_MODE=api
EXPOSE ${PORT}

CMD ["./entrypoint.sh"]
