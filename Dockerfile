# Stage 1: Build Python wheels
FROM python:3.13-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt

# Stage 2: Compile Tailwind CSS
FROM node:20-alpine AS node
WORKDIR /build
COPY package.json package-lock.json tailwind.config.js ./
COPY src/templates/ src/templates/
COPY src/core/ src/core/
COPY src/static/css/input.css src/static/css/input.css
RUN npm ci && npx tailwindcss -i src/static/css/input.css -o src/static/css/styles.css --minify

# Stage 3: Runtime
FROM python:3.13-slim AS final

# WeasyPrint + libmagic (file-upload magic-bytes validation, Refs #610) system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 \
    libcairo2 libgdk-pixbuf-2.0-0 libffi-dev \
    libglib2.0-0 shared-mime-info \
    fontconfig fonts-dejavu-core \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages from wheels
COPY --from=builder /build/wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/* && rm -rf /tmp/wheels

# Copy application code
COPY src/ ./

# Copy compiled Tailwind CSS
COPY --from=node /build/src/static/css/styles.css static/css/styles.css

# App version (set at build time, exposed via /health/)
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

# Collect static files (needs a dummy secret key)
ARG DJANGO_SECRET_KEY=build-only-placeholder
ENV DJANGO_SETTINGS_MODULE=anlaufstelle.settings.prod
RUN DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY python manage.py collectstatic --noinput
ENV DJANGO_SECRET_KEY=

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

RUN adduser --disabled-password --gecos '' --uid 1000 appuser && chown -R appuser:appuser /app

# MEDIA_ROOT-Mount-Punkt vorbereiten: Docker-Named-Volumes erben die
# Permissions des Mount-Targets im Image. /data/media muss daher als
# appuser-owned vorab existieren, damit das Volume beim ersten Mount
# nicht als root angelegt wird (sonst kein Schreibzugriff fuer den
# appuser, kein Datei-Upload moeglich). Refs #720, Refs #733.
RUN mkdir -p /data/media && chown -R appuser:appuser /data

USER appuser

EXPOSE 8000

# Healthcheck gegen /health/ — 30s Intervall, 5s Timeout, 10s Grace für
# Startup. Für Deployments ohne docker-compose (plain docker run, k8s,
# Coolify etc.), wo der Compose-Healthcheck nicht greift. Refs #654.
#
# Refs #798 (C-30): wir lesen jetzt zusaetzlich den JSON-``status``-Schluessel.
# Bei ``status=degraded`` (z.B. ClamAV ausgefallen) liefert /health/ HTTP 200,
# damit Last-Balancer den Pod nicht direkt rauswerfen — der Container-Healthcheck
# soll das aber trotzdem als ungesund markieren, damit Operator-Tooling
# (Coolify, k8s) den Vorfall sieht.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, json, sys; \
body = urllib.request.urlopen('http://localhost:8000/health/', timeout=5).read(); \
sys.exit(0 if json.loads(body).get('status') == 'ok' else 1)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
