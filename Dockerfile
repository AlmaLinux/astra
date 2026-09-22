FROM python:3.14-slim AS base

ARG ASTRA_BUILD_SHA=""
ENV ASTRA_BUILD_SHA=$ASTRA_BUILD_SHA

# Install system dependencies for Postgres, Pillow, and in-container JS execution tests.
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    tzdata-legacy \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/astra_app

COPY requirements.txt .

# Resolution-only stage, built by `scripts/update-requirements-lock.sh`, which
# freezes it into requirements.lock. It shares `base` so versions are resolved
# against the same Python and system libraries the app image actually uses.
FROM base AS resolve-deps
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS app

# requirements.txt declares intent; requirements.lock pins every resolved
# version, transitive ones included, so a rebuild installs what was tested
# rather than whatever is newest on the day.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock

# Keep entrypoint outside the bind-mounted /app volume (devcontainers/compose)
COPY docker/entrypoint.sh /usr/local/bin/astra-entrypoint
COPY docker/migrate.sh /usr/local/bin/migrate.sh
RUN chmod +x /usr/local/bin/astra-entrypoint /usr/local/bin/migrate.sh

COPY . .

RUN cd frontend && npm ci && npm run build

# Collect static files for production.
# This intentionally runs at build time so the runtime container can serve
# `/static/` via WhiteNoise without requiring any writable volume.
RUN cd astra_app && python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/astra-entrypoint"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--config", "/app/astra_app/gunicorn.conf.py"]
