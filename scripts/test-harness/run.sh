#!/usr/bin/env bash
# TEST-SAFETY-0.1: the only supported path for destructive UTU/Genesis integration tests.
#
# Establishes the disposable boundary and declares it to the tests:
# - a dedicated --internal Docker network (no route to other networks);
# - fresh MongoDB, application Redis and UTU test Redis containers on it only, with
#   tmpfs data (no volumes) and no published ports;
# - a test runner on that network only, with the repository source mounted read-only
#   and an explicit environment (never .env: no live configuration or credentials);
# - the UTU Redis process identity (INFO server run_id), read from the container this
#   script created and passed as UTU_TEST_REDIS_RUN_ID.
# Every container's networks, ports, mounts and harness label are verified before the
# tests start; everything the run created is removed on exit.
#
# Usage: scripts/test-harness/run.sh [pytest args...]     (default: src)
#        CAOS_TEST_PARALLEL=N scripts/test-harness/run.sh ...   (N runners, one Redis)
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${CAOS_TEST_IMAGE:-caos-api-api}"
PARALLEL="${CAOS_TEST_PARALLEL:-1}"
random_hex() { od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; }
RUN="caos-th-$(random_hex 6)"
NET="$RUN-net"
MONGO="$RUN-mongo"
APP_REDIS="$RUN-redis-app"
UTU_REDIS="$RUN-redis-utu"
LABEL_KEY="caos.test-harness"
TMP_ROOT="${TMPDIR:-/tmp}"
LOG_ROOT="${CAOS_TEST_LOG_DIR:-${TMP_ROOT%/}/caos-test-harness}"
LOG_DIR="${LOG_ROOT%/}/$RUN"
RUNNERS=()

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cleanup() {
  local status=$? name leftover
  for name in "${RUNNERS[@]+"${RUNNERS[@]}"}" "$MONGO" "$APP_REDIS" "$UTU_REDIS"; do
    docker rm -f -v "$name" >/dev/null 2>&1 || true
  done
  docker network rm "$NET" >/dev/null 2>&1 || true
  # Nothing this run created may survive it.
  leftover="$(docker ps -aq --filter "label=$LABEL_KEY=$RUN")$(docker network ls -q --filter "label=$LABEL_KEY=$RUN")"
  if [[ -n "$leftover" ]]; then
    echo "FAIL: harness resources of $RUN remain after cleanup" >&2
    exit 3
  fi
  exit "$status"
}
trap cleanup EXIT
# An interrupted run still cleans up (EXIT trap) and never reports success.
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "$PARALLEL" =~ ^[1-8]$ ]] || fail "CAOS_TEST_PARALLEL must be 1-8"
docker image inspect "$IMAGE" >/dev/null 2>&1 || fail "Test image $IMAGE is not built"
[[ $# -gt 0 ]] || set -- src

docker network create --internal --label "$LABEL_KEY=$RUN" "$NET" >/dev/null
docker run -d --name "$MONGO" --network "$NET" --label "$LABEL_KEY=$RUN" \
  --tmpfs /data/db --tmpfs /data/configdb mongo:8.0 >/dev/null
for redis in "$APP_REDIS" "$UTU_REDIS"; do
  docker run -d --name "$redis" --network "$NET" --label "$LABEL_KEY=$RUN" \
    --tmpfs /data redis:6-alpine redis-server --save "" --appendonly no >/dev/null
done

verify_container() {
  local name="$1" expected_mounts="$2" networks ports mounts label
  networks="$(docker inspect -f '{{range $n, $_ := .NetworkSettings.Networks}}{{$n}} {{end}}' "$name")"
  [[ "$networks" == "$NET " ]] || fail "$name is attached to: $networks"
  ports="$(docker inspect -f '{{json .HostConfig.PortBindings}}|{{.HostConfig.PublishAllPorts}}' "$name")"
  [[ "$ports" == "{}|false" || "$ports" == "null|false" ]] || fail "$name publishes ports: $ports"
  label="$(docker inspect -f "{{index .Config.Labels \"$LABEL_KEY\"}}" "$name")"
  [[ "$label" == "$RUN" ]] || fail "$name is not owned by this harness run"
  mounts="$(docker inspect -f '{{range .Mounts}}{{.Type}}:{{.Destination}}:{{.RW}}{{"\n"}}{{end}}' "$name" \
    | sed '/^$/d' | sort | tr '\n' ' ')"
  [[ "$mounts" == "$expected_mounts" ]] || fail "$name has unexpected mounts: $mounts"
}

[[ "$(docker network inspect -f '{{.Internal}}' "$NET")" == "true" ]] || fail "$NET is not internal"
for name in "$MONGO" "$APP_REDIS" "$UTU_REDIS"; do
  verify_container "$name" ""
done

for _ in $(seq 1 60); do
  ready=0
  for redis in "$APP_REDIS" "$UTU_REDIS"; do
    [[ "$(docker exec "$redis" redis-cli ping 2>/dev/null | tr -d '\r')" == "PONG" ]] && ready=$((ready + 1))
  done
  docker exec "$MONGO" mongosh --quiet --eval 'db.runCommand({ping: 1}).ok' 2>/dev/null \
    | grep -q '^1$' && ready=$((ready + 1))
  [[ $ready -eq 3 ]] && break
  sleep 1
done
[[ ${ready:-0} -eq 3 ]] || fail "Disposable services did not become ready"

# The identity of the Redis process this script created (not settable by clients).
RUN_ID="$(docker exec "$UTU_REDIS" redis-cli INFO server | tr -d '\r' | sed -n 's/^run_id://p')"
[[ "$RUN_ID" =~ ^[0-9a-f]{40}$ ]] || fail "Could not read the UTU Redis run_id"
SIGNING_KEY="$(random_hex 32)"

for index in $(seq 1 "$PARALLEL"); do
  runner="$RUN-runner-$index"
  docker create --name "$runner" --network "$NET" --label "$LABEL_KEY=$RUN" \
    -e DB_URI="mongodb://$MONGO:27017" -e DB_NAME=caos_test_harness \
    -e OPS2_MONGO_INTEGRATION=1 \
    -e REDIS_URL="redis://$APP_REDIS:6379/0" -e REDIS_HOST="$APP_REDIS" \
    -e REDIS_PORT=6379 -e REDIS_TLS=false \
    -e UTU_TEST_REDIS_URL="redis://$UTU_REDIS:6379/15" \
    -e UTU_TEST_REDIS_RUN_ID="$RUN_ID" -e CAOS_TEST_HARNESS="$RUN" \
    -e COGNITIVE_EVENT_SIGNING_KEY="$SIGNING_KEY" -e RABBITMQ_HOST=127.0.0.1 \
    -e AUTHZ_ENABLED=false -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$ROOT/src:/app/src:ro" -v "$ROOT/config:/app/config:ro" \
    -v "$ROOT/pyproject.toml:/app/pyproject.toml:ro" -w /app \
    "$IMAGE" python -m pytest -p no:cacheprovider --disable-warnings -q -rfE "$@" >/dev/null
  RUNNERS+=("$runner")
  verify_container "$runner" "bind:/app/config:false bind:/app/pyproject.toml:false bind:/app/src:false "
done

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR"
echo "CAOS disposable test harness $RUN (internal network, $PARALLEL runner(s)); logs: $LOG_DIR"

# One summary line per runner: identity, timestamps, exit code and failure category.
# Logs hold pytest output only: no environment, credentials or authority values.
classify() {
  local code="$1" log="$2" statuses
  statuses="$(grep -oE 'assert \(?[0-9]{3} ==|status_code == [0-9]{3}' "$log" 2>/dev/null \
    | grep -oE '[0-9]{3}' | sort -u | tr '\n' ',' | sed 's/,$//')"
  if [[ "$code" == 0 ]]; then
    echo "passed"
  elif grep -q 'UnsafeVerificationTarget' "$log"; then
    echo "refused_unsafe_target"
  elif grep -qE 'UTU scope revoked or unprovisioned|UTU execution permit superseded' "$log"; then
    echo "utu_authority_denied${statuses:+ http=$statuses}"
  elif [[ "$code" == 1 ]]; then
    echo "test_failure${statuses:+ http=$statuses}"
  elif [[ "$code" == 5 ]]; then
    echo "no_tests_collected"
  else
    echo "harness_or_runtime_error"
  fi
}

record() {
  local runner="$1" code started finished category
  shift
  code="$(docker inspect -f '{{.State.ExitCode}}' "$runner")"
  started="$(docker inspect -f '{{.State.StartedAt}}' "$runner")"
  finished="$(docker inspect -f '{{.State.FinishedAt}}' "$runner")"
  category="$(classify "$code" "$LOG_DIR/$runner.log")"
  printf 'run=%s runner=%s started=%s finished=%s exit=%s category=%s args=%s\n' \
    "$RUN" "${runner#"$RUN"-}" "$started" "$finished" "$code" "$category" "$*" \
    | tee -a "$LOG_DIR/summary.txt" >"$LOG_DIR/$runner.summary"
  [[ "$code" == 0 ]]
}

status=0
pids=()
for runner in "${RUNNERS[@]}"; do
  if [[ "$PARALLEL" -eq 1 ]]; then
    docker start -a "$runner" 2>&1 | tee "$LOG_DIR/$runner.log" || true
  else
    docker start -a "$runner" >"$LOG_DIR/$runner.log" 2>&1 &
    pids+=("$!")
  fi
done
for pid in "${pids[@]+"${pids[@]}"}"; do
  wait "$pid" || true
done
for runner in "${RUNNERS[@]}"; do
  record "$runner" "$@" || status=1
done
if [[ "$status" -ne 0 ]]; then
  # Preserve the disposable services' own logs before cleanup removes them.
  mkdir -p "$LOG_DIR/services"
  for name in "$MONGO" "$APP_REDIS" "$UTU_REDIS"; do
    docker logs "$name" >"$LOG_DIR/services/${name#"$RUN"-}.log" 2>&1 || true
  done
fi
cat "$LOG_DIR/summary.txt"
[[ "$status" -ne 0 ]] || [[ -n "${CAOS_TEST_KEEP_LOGS:-}" ]] || rm -rf "$LOG_DIR"
exit "$status"
