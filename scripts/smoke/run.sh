
#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="$ROOT/docker-compose.smoke.yml"
CREDENTIALS_FILE="$ROOT/.smoke/credentials.env"

compose() {
  docker compose \
    --env-file "$CREDENTIALS_FILE" \
    -f "$COMPOSE_FILE" \
    "$@"
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# Never take ownership of an existing smoke environment.
# This includes stopped containers from previous runs.
if [[ -f "$CREDENTIALS_FILE" ]]; then
  existing="$(compose ps --all -q)"
  [[ -z "$existing" ]] || fail \
    "Smoke containers already exist. Inspect and remove them explicitly before retrying."
else
  # The Compose project may already exist even without this credentials file.
  existing="$(docker compose -f "$COMPOSE_FILE" \
    --project-name caos-ncsc-smoke ps --all -q 2>/dev/null || true)"
  [[ -z "$existing" ]] || fail \
    "Existing smoke containers detected without credentials. Refusing to modify them."
fi

# Verify the production source hasn't been modified.
git diff --quiet HEAD -- \
  src entrypoint.py pyproject.toml uv.lock Dockerfile docker-compose.yml \
  ':(exclude)src/**/tests/**' \
  || fail "Uncommitted production source changes detected"

untracked="$(
  git ls-files --others --exclude-standard -- src |
    grep -v '/tests/' || true
)"

[[ -z "$untracked" ]] || fail \
  "Untracked production source detected"

export SMOKE_COMMIT
SMOKE_COMMIT="$(git rev-parse HEAD)"

echo "CAOS NCSC smoke validation"
echo "Candidate: $SMOKE_COMMIT"

# Generate credentials only if they don't already exist.
python3 scripts/smoke/generate_credentials.py

[[ -f "$CREDENTIALS_FILE" ]] || fail \
  "Smoke credential generation failed"

# Validate Compose configuration without printing secrets.
compose config --quiet

started=false

cleanup() {
  result=$?
  trap - EXIT INT TERM

  if [[ "$started" == true ]]; then
    echo "Cleaning up isolated NCSC smoke environment..."
    compose down --volumes --remove-orphans \
      || echo "WARNING: Smoke infrastructure cleanup failed" >&2
  fi

  if [[ $result -eq 0 ]]; then
    echo "PASS: NCSC smoke environment completed"
  else
    echo "FAIL: NCSC smoke environment exited with code $result" >&2
  fi

  exit "$result"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Starting isolated RabbitMQ and CAOS smoke runner..."

started=true

# Compose starts RabbitMQ, runs provisioning, then executes the smoke
# runner. The selected service's exit code becomes this command's result.
echo "Starting isolated RabbitMQ..."
compose up -d --wait rabbitmq

echo "Provisioning isolated RabbitMQ resources..."
compose run --rm --no-deps provision

echo "Building CAOS smoke runner..."
compose build smoke

echo "Executing live NCSC integration test..."
compose run --rm --no-deps smokemoke
