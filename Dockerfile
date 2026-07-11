# Stage 1: Build Python wheels
# Base-Images per Digest gepinnt (Refs #1373): der Tag bleibt lesbar, der
# @sha256 friert die Manifest-Liste (multi-arch) ein — ein umgehaengtes Tag
# (Index-/Registry-MITM) kann so keinen anderen Inhalt unterschieben.
# Bump: `docker buildx imagetools inspect <image>` -> .Manifest.Digest.
FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS builder
WORKDIR /build
COPY requirements.txt .
# --require-hashes verifiziert JEDES von PyPI geladene Artefakt gegen die
# in requirements.txt (pip-compile --generate-hashes) gepinnten sha256-Hashes.
# Hier — am Download/Wheel-Bau — sitzt die Integritaetsgarantie der App-Deps.
RUN pip wheel --no-cache-dir --require-hashes --wheel-dir /build/wheels -r requirements.txt

# Stage 2: Compile Tailwind CSS
FROM node:26-alpine@sha256:725aeba2364a9b16beae49e180d83bd597dbd0b15c47f1f28875c290bfd255b9 AS node
WORKDIR /build
COPY package.json package-lock.json tailwind.config.js ./
COPY src/templates/ src/templates/
COPY src/core/ src/core/
COPY src/static/css/input.css src/static/css/input.css
RUN npm ci && npx @tailwindcss/cli -i src/static/css/input.css -o src/static/css/styles.css --minify

# Stage 3: Runtime
FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS final

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
# --no-index + --no-deps: es wird AUSSCHLIESSLICH aus dem lokalen Wheel-Ordner
# installiert (kein PyPI-Zugriff mehr). Kein erneutes --require-hashes hier, weil
# lokal aus sdists gebaute Wheels andere sha256-Hashes haben als die PyPI-Artefakte;
# die Hash-Pruefung ist bereits im Builder (pip wheel --require-hashes) erfolgt.
# Der Wheel-Ordner enthaelt die vollstaendige transitive Huelle, daher ist
# --no-deps korrekt und schliesst jeden Netzwerk-Fallback aus. Refs #1373.
COPY --from=builder /build/wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index --no-deps /tmp/wheels/* && rm -rf /tmp/wheels

# Copy application code
COPY src/ ./

# pyproject.toml fuer die Versionsanzeige (Refs #1504): health.py liest
# ``BASE_DIR.parent/pyproject.toml`` fuer die SemVer-Anzeige im Footer und
# im System-Health-Dashboard. BASE_DIR liegt hier (WORKDIR /app, src/
# unmittelbar hineinkopiert) auf /app, also .parent == / -- Zielpfad muss
# exakt /pyproject.toml sein, sonst FileNotFoundError-Fallback auf den
# APP_VERSION-ENV-Wert (samt ERROR-Log-Rauschen vor Refs #1504).
COPY pyproject.toml /pyproject.toml

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
COPY docker-migrate.sh /app/docker-migrate.sh
RUN chmod +x /app/docker-entrypoint.sh /app/docker-migrate.sh

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
