FROM python:3.11-slim-bookworm

# Install system dependencies and Playwright requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    wget \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install "scrapling[fetchers]" \
    && python -m playwright install --with-deps chromium \
    && scrapling install

COPY src/ ./src/
COPY entrypoint.sh start.sh ./
RUN chmod +x entrypoint.sh start.sh

ENV PORT=8080
ENV SERVICE_MODE=api
EXPOSE ${PORT}

CMD ["./entrypoint.sh"]
