FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies from requirements.txt (faster, no setuptools needed)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application code and startup script
COPY src/ ./src/
COPY start.sh ./

# Railway injects PORT at runtime
ENV PORT=8000
EXPOSE ${PORT}

CMD ["./start.sh"]
