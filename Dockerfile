# Stage 1: Build Python wheels
FROM python:3.13-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt

# Stage 2: Compile Tailwind CSS
FROM node:20-alpine AS node
WORKDIR /build
COPY package.json tailwind.config.js ./
COPY src/templates/ src/templates/
COPY src/core/ src/core/
COPY src/static/css/input.css src/static/css/input.css
RUN npm install && npx tailwindcss -i src/static/css/input.css -o src/static/css/styles.css --minify

# Stage 3: Runtime
FROM python:3.13-slim AS final

# WeasyPrint system dependencies (Debian Bookworm)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 \
    libcairo2 libgdk-pixbuf-2.0-0 libffi-dev \
    libglib2.0-0 shared-mime-info \
    fontconfig fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages from wheels
COPY --from=builder /build/wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/* && rm -rf /tmp/wheels

# Copy application code
COPY src/ ./

# Copy compiled Tailwind CSS
COPY --from=node /build/src/static/css/styles.css static/css/styles.css

# Collect static files (needs a dummy secret key)
ARG DJANGO_SECRET_KEY=build-only-placeholder
ENV DJANGO_SETTINGS_MODULE=anlaufstelle.settings.prod
RUN DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY python manage.py collectstatic --noinput
ENV DJANGO_SECRET_KEY=

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

RUN adduser --disabled-password --gecos '' --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
