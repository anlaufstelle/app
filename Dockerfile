# Stage 1: Build Python wheels
# Base-Images per Digest gepinnt (Refs #1373): der Tag bleibt lesbar, der
# @sha256 friert die Manifest-Liste (multi-arch) ein — ein umgehaengtes Tag
# (Index-/Registry-MITM) kann so keinen anderen Inhalt unterschieben.
# Bump: `docker buildx imagetools inspect <image>` -> .Manifest.Digest.
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS builder
WORKDIR /build
COPY requirements.txt .
# --require-hashes verifiziert JEDES von PyPI geladene Artefakt gegen die
# in requirements.txt (pip-compile --generate-hashes) gepinnten sha256-Hashes.
# Hier — am Download/Wheel-Bau — sitzt die Integritaetsgarantie der App-Deps.
RUN pip wheel --no-cache-dir --require-hashes --wheel-dir /build/wheels -r requirements.txt

# supercronic — Cron-Daemon des ``cron``-Sidecars (Refs #794, C-26).
# Container-tauglicher Ersatz fuer cron/crond: laeuft im Vordergrund, loggt
# nach stdout/stderr, beendet auf SIGTERM sauber und braucht kein setuid.
# Das Binary wird versions- UND sha256-gepinnt geladen; die Pins und die
# Verifikation stehen in ``deploy/fetch_supercronic.py`` (dieselbe
# Integritaetsgarantie wie ``pip wheel --require-hashes`` eine Zeile darueber).
# TARGETARCH liefert buildx pro Zielplattform (linux/amd64, linux/arm64 —
# siehe .github/workflows/release.yml); Default fuer plain ``docker build``.
ARG TARGETARCH
COPY deploy/fetch_supercronic.py /build/fetch_supercronic.py
RUN python /build/fetch_supercronic.py --arch "${TARGETARCH:-amd64}" --output /build/supercronic

# Stage 2: Compile Tailwind CSS
FROM node:26-alpine@sha256:233761595746769ebfdb6090f44fc7cdf818ae0ce62d2b37e0367723b9823e36 AS node
WORKDIR /build
COPY package.json package-lock.json tailwind.config.js ./
COPY src/templates/ src/templates/
COPY src/core/ src/core/
# Per JavaScript erzeugte Elemente (Hinweis-Toasts, Offline-Ansichten) tragen
# Klassen, die nur in src/static/js/ stehen — ohne diese Quellen fehlen sie
# in der ausgelieferten styles.css (Refs #2387).
COPY src/static/js/ src/static/js/
COPY src/static/css/input.css src/static/css/input.css
RUN npm ci && npx @tailwindcss/cli -i src/static/css/input.css -o src/static/css/styles.css --minify

# Stage 3: Runtime
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS final

# WeasyPrint + libmagic (file-upload magic-bytes validation, Refs #610) system dependencies
# libharfbuzz-subset0: von WeasyPrint >= 70 beim Import geladen (Font-Subsetting);
# ohne das Paket DeprecationWarning, kuenftige Versionen setzen es voraus.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
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

# supercronic-Binary aus dem Builder uebernehmen (dort gegen den sha256-Pin
# verifiziert, Mode 0755). Nur der ``cron``-Sidecar startet es; fuer den
# web-Container ist es totes Gewicht von ~16 MB, aber ein zweites Image
# waere mehr Betriebs-/Release-Aufwand als der Platz wert (Refs #794).
COPY --from=builder /build/supercronic /usr/local/bin/supercronic

# Copy application code
COPY src/ ./

# pyproject.toml fuer die Versionsanzeige (Refs #1504): health.py liest
# ``BASE_DIR.parent/pyproject.toml`` fuer die SemVer-Anzeige im Footer und
# im System-Health-Dashboard. BASE_DIR liegt hier (WORKDIR /app, src/
# unmittelbar hineinkopiert) auf /app, also .parent == / -- Zielpfad muss
# exakt /pyproject.toml sein, sonst FileNotFoundError-Fallback auf den
# APP_VERSION-ENV-Wert (samt ERROR-Log-Rauschen vor Refs #1504).
COPY pyproject.toml /pyproject.toml

# Copy compiled Tailwind CSS. Die zweite Ablage ausserhalb /app bleibt auch
# dann sichtbar, wenn die Dev-Compose den Quellbaum auf /app bind-mountet. Der
# Dev-Start kopiert dieses Artefakt in den gemounteten Baum (Refs #1977).
COPY --from=node /build/src/static/css/styles.css /opt/anlaufstelle/static/css/styles.css
RUN mkdir -p static/css && cp /opt/anlaufstelle/static/css/styles.css static/css/styles.css

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

# Named volumes inherit their mount target's initial content and ownership.
# Compose mounts /data read-only in web and read-write in maintenance (Refs #2527).
# Prepare separate roots; web uploads remain writable under /media/media.
RUN mkdir -p /media/media /data && chown -R appuser:appuser /media /data

USER appuser

EXPOSE 8000

# Healthcheck gegen /health/ — 30s Intervall, 5s Timeout, 10s Grace für
# Startup. Für Deployments ohne docker-compose (plain docker run, k8s
# etc.), wo der Compose-Healthcheck nicht greift. Refs #654.
#
# Refs #798 (C-30): wir lesen jetzt zusaetzlich den JSON-``status``-Schluessel.
# Bei ``status=degraded`` (z.B. ClamAV ausgefallen) liefert /health/ HTTP 200,
# damit Last-Balancer den Pod nicht direkt rauswerfen — der Container-Healthcheck
# soll das aber trotzdem als ungesund markieren, damit Operator-Tooling
# (k8s, eigene Orchestrierung) den Vorfall sieht.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, json, sys; \
body = urllib.request.urlopen('http://localhost:8000/health/', timeout=5).read(); \
sys.exit(0 if json.loads(body).get('status') == 'ok' else 1)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
