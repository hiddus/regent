# Local Development

## Requirements

- Python 3.12
- PostgreSQL 16 (required from S1)
- Docker Compose is optional for S0 and recommended for database integration

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

## Quality gate

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\pytest.exe
.\.venv\Scripts\alembic.exe upgrade head --sql
```

## Run locally

```powershell
.\.venv\Scripts\regent-api.exe
.\.venv\Scripts\regent-worker.exe
```

The API exposes `/health/live`, `/health/ready`, and `/openapi.json`.
The S0 worker intentionally performs no durable work. Queue claiming begins in S1.
