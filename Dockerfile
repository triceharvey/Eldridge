# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
ARG PYTHON_IMAGE=python:3.11.13-alpine3.22@sha256:801053a2c35d91edc335c4c478f682de905611fed131007715538c7552d943db

FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --disable-pip-version-check --wheel-dir /wheels .

FROM ${PYTHON_IMAGE} AS runtime-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/control-plane/.local/bin:${PATH}
RUN addgroup -g 65532 control-plane \
    && adduser -D -H -u 65532 -G control-plane control-plane \
    && apk add --no-cache ca-certificates \
    && mkdir -p /app /workspaces \
    && chown -R 65532:65532 /app /workspaces
COPY --from=builder /wheels /wheels
RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-index \
      --find-links=/wheels ai-engineering-control-plane \
    && rm -rf /wheels
WORKDIR /app
USER 65532:65532

FROM runtime-base AS worker
USER root
RUN apk add --no-cache docker-cli git
USER 65532:65532
CMD ["control-plane-worker"]

FROM runtime-base AS api
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["uvicorn", "control_plane.api:app", "--host", "0.0.0.0", "--port", "8000"]
