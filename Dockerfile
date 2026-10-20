FROM python:3.11-slim-bookworm

WORKDIR /app

COPY pyproject.toml ./
COPY uv.lock ./

RUN python -m pip install --upgrade pip \
    && python -m pip install --upgrade "setuptools>=80.10.1" "wheel>=0.46.2" "uv" \
    && uv pip install --system -e ".[test]"

COPY . .

RUN chmod +x entrypoint.py

EXPOSE 8000

ARG APP_VERSION
ENV APP_VERSION=${APP_VERSION}
ENV PYTHONPATH=/app

CMD ["python", "entrypoint.py"]