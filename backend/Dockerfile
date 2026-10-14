FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
# mmh3 (a fastembed dependency) has no prebuilt wheel for slim Linux, so compile it, then drop the toolchain
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY . .
# Pre-download the embedding model into the image so the first request isn't slow
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
