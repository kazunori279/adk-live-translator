FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /workspace

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

WORKDIR /workspace/app

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
