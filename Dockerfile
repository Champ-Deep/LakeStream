FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Install Playwright browsers
RUN playwright install --with-deps chromium

COPY src/ ./src/

EXPOSE 3000

CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "3000"]
