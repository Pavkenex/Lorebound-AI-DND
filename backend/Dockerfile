# Backend image. BUILD CONTEXT = REPO ROOT (see docker-compose.yml, and the
# Coolify service config: base directory = repo root, Dockerfile = backend/Dockerfile).
# The rebuilt engine (engine/, stdlib-only, py3.13) ships inside this image and is
# imported in-process — no separate service (docs/INTEGRATION_PLAN.md §1).
FROM python:3.13-slim
WORKDIR /code
COPY engine /opt/engine
COPY backend/pyproject.toml backend/README.md ./
COPY backend/app ./app
RUN pip install --no-cache-dir -e . /opt/engine
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
