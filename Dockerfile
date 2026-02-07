FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies from requirements.txt (faster, no setuptools needed)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Railway injects PORT at runtime
ENV PORT=8000
EXPOSE ${PORT}

CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
