FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy everything so pip install can find src/ package
COPY pyproject.toml ./
COPY src/ ./src/
COPY tests/ ./tests/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Railway injects PORT at runtime
ENV PORT=8000
EXPOSE ${PORT}

CMD uvicorn src.server:app --host 0.0.0.0 --port $PORT
