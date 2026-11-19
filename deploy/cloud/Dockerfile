# Build from the repository root:  docker build -f deploy/cloud/Dockerfile -t jarvis-sidekick .
FROM python:3.13-slim

ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[resume,apply,youtube]" \
    && playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 sidekick \
    && mkdir -p /data && chown sidekick /data

USER sidekick
ENV SIDEKICK_HOME=/data
VOLUME /data
# No EXPOSE: the bot only makes outbound connections.
CMD ["sidekick", "bot"]
