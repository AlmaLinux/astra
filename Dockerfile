FROM python:3.14-slim

ARG ASTRA_BUILD_SHA=""
ENV ASTRA_BUILD_SHA=$ASTRA_BUILD_SHA

# Install system dependencies for Postgres and Pillow
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/astra_app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Keep entrypoint outside the bind-mounted /app volume (devcontainers/compose)
COPY docker/entrypoint.sh /usr/local/bin/astra-entrypoint
COPY docker/migrate.sh /usr/local/bin/migrate.sh
RUN chmod +x /usr/local/bin/astra-entrypoint /usr/local/bin/migrate.sh

COPY . .

# Collect static files for production.
# This intentionally runs at build time so the runtime container can serve
# `/static/` via WhiteNoise without requiring any writable volume.
RUN cd astra_app && python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/astra-entrypoint"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "--capture-output", "--log-level", "info", "--forwarded-allow-ips", "*", "--access-logformat", "%({x-forwarded-for}i)s %(l)s %(u)s %(t)s \"%(r)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\""]
