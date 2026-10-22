
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install build tooling
RUN python -m pip install --upgrade pip \
    && python -m pip install \
        "setuptools>=80.10.1" \
        "wheel>=0.46.2" \
        uv

# Copy project before installing it
COPY pyproject.toml uv.lock ./
COPY . .

# Git-derived version, supplied by CI or Docker Compose
ARG APP_VERSION=0.0.0.dev0
ARG APP_REVISION=unknown
ARG APP_CHANNEL=development

LABEL org.opencontainers.image.title="CAOS API" \
      org.opencontainers.image.description="Cognitive Agentic Operative System API" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${APP_REVISION}" \
      org.opencontainers.image.source="https://github.com/gjguidos/caos-api"
      
# Build-time version for setuptools-scm
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CAOS_API=${APP_VERSION}

# Runtime version metadata
ENV APP_VERSION=${APP_VERSION}
ENV APP_REVISION=${APP_REVISION}
ENV APP_CHANNEL=${APP_CHANNEL}
ENV PYTHONPATH=/app

# Install project and development/test dependencies once
RUN uv pip install --system -e ".[test]"

RUN chmod +x entrypoint.py

EXPOSE 8000

CMD ["python", "entrypoint.py"]
