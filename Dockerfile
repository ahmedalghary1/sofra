FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.container_settings
WORKDIR /app

# Use a PostgreSQL 16 client for the existing backup and restore commands.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && mkdir -p /usr/share/postgresql-common/pgdg \
    && curl --fail --silent --show-error https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && printf '%s\n' 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y --auto-remove curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir -p /app/staticfiles /app/media /backups /run/gunicorn \
    && chown -R app:app /app/staticfiles /app/media /backups /run/gunicorn
# Explicit allowlist: never copy .env, databases, uploads, backups or Git history.
COPY manage.py ./
COPY config ./config
COPY core ./core
COPY templates ./templates
COPY static ./static
COPY docker ./docker
COPY scripts/configure_docker.py ./scripts/
USER 10001:10001
ENTRYPOINT ["python", "docker/entrypoint.py"]
CMD ["web"]
