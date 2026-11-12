# Turntable-1: one image, four roles (icecast, capture, core, web); see docker-compose.yml.
FROM python:3.12-slim

# ffmpeg reads the turntable and encodes the stream; icecast2 serves it; tini forwards signals
# so containers stop cleanly.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg icecast2 alsa-utils tini \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 1000 --create-home turntable && usermod -aG audio turntable

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY turntable-id/ /app/core/
COPY turntable-web/ /app/web/
COPY docker/ /app/docker/

# the version, from the release tag (or main plus the commit), shown on the settings page
ARG VERSION=dev
LABEL org.opencontainers.image.title="Turntable-1" org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/TorinML/turntable-1"

ENV TURNTABLE_CONFIG_DIR=/config \
    TURNTABLE_CORE=/app/core \
    TURNTABLE_WEB=/app/web \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TURNTABLE_VERSION=${VERSION}

USER turntable
VOLUME ["/config", "/data"]
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/docker/entrypoint.py"]
CMD ["web"]
