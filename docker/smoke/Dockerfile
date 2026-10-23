
# Stage 1: Install Python dependencies separately from the verified source.
FROM python:3.11-slim-bookworm AS builder

WORKDIR /app

RUN python -m pip install --no-cache-dir uv \
    && python -m venv /opt/venv

COPY pyproject.toml uv.lock ./
COPY . .

ARG APP_VERSION=0.0.0.dev0
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CAOS_API=${APP_VERSION}

RUN uv pip install \
    --python /opt/venv/bin/python \
    -e ".[test]"


# Stage 2: Recreate the runtime with an untouched source snapshot.
FROM python:3.11-slim-bookworm AS smoke

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Copy source only after dependency installation.
COPY . .

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1

ARG APP_VERSION=0.0.0.dev0
ARG APP_REVISION=unknown

ENV APP_VERSION=${APP_VERSION} \
    APP_REVISION=${APP_REVISION}

CMD ["python", "-B", "-m", "src.base.events.v2.tests.live_rabbitmq_smoke", "--help"]
