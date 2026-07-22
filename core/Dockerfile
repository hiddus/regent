FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY core ./core
COPY apps ./apps
COPY docs ./docs
RUN pip install --no-cache-dir --timeout 180 --retries 10 --index-url "$PIP_INDEX_URL" .

USER nobody
EXPOSE 8000
CMD ["regent-api"]
