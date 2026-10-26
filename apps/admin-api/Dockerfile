FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# `embeddings` pulls torch + FlagEmbedding for local BGE-M3. Mount the model
# snapshot at runtime and point BGE_M3_PATH at it.
RUN pip install --upgrade pip && pip install ".[milvus,embeddings]"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
