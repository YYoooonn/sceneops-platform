#!/usr/bin/env bash
# Read-only disk report of the local SceneOps runtime (`make disk-report`).
#
# Reports where the space is and what is reclaimable; removes nothing. The cleanup
# policy that interprets it is docs/development/local-development.md#disk-hygiene.
# Every Docker query tolerates a stopped daemon or stack and says so.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
ENV_FILE="${ENV_FILE:-.env.local}"
KAFKA_CONTAINER="${KAFKA_CONTAINER:-sceneops-kafka-1}"
MIN_FREE_GIB="${MIN_FREE_GIB:-6}"   # the streaming bootstrap's own per-fixture floor

section() { printf '\n== %s\n' "$1"; }

section "host volume"
df -h "$REPO_ROOT" | awk 'NR==1 || NR==2'
free_gib="$(df -Pk "$REPO_ROOT" | awk 'NR==2 { printf "%.1f", $4 / 1048576 }')"
echo "free: ${free_gib} GiB (streaming-bootstrap stops below ${MIN_FREE_GIB} GiB per fixture)"

section "generated and preserved directories (data/, cache/)"
du -sh data/* cache/* 2>/dev/null | sort -h
echo "preserved, never reclaimed here: data/raw, data/reference, config/reference"

if ! docker info >/dev/null 2>&1; then
  echo; echo "docker daemon not reachable: Docker sections skipped"
  exit 0
fi

section "docker usage"
docker system df

section "SceneOps volumes (sceneops_*)"
docker system df -v --format '{{json .Volumes}}' 2>/dev/null \
  | python3 -c '
import json, sys
for v in sorted(json.load(sys.stdin) or [], key=lambda v: v["Name"]):
    if v["Name"].startswith("sceneops_"):
        print("  %-40s %10s  links=%s" % (v["Name"], v["Size"], v["Links"]))
' || docker volume ls --filter name=sceneops_
echo "  golden state, never reclaimed here: sceneops_minio-data, sceneops_postgres-data"

section "Kafka log"
if docker ps --format '{{.Names}}' | grep -qx "$KAFKA_CONTAINER"; then
  docker exec "$KAFKA_CONTAINER" sh -c 'du -sh /var/lib/kafka/data/sceneops.* 2>/dev/null; du -sh /var/lib/kafka/data'
  docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-configs.sh --bootstrap-server localhost:9092 \
    --describe --all --entity-type topics --entity-name sceneops.robot.telemetry.v1 2>/dev/null \
    | grep -E "^ *(retention\.ms|segment\.bytes)=" | awk '{print "  " $1}'
else
  echo "  $KAFKA_CONTAINER is not running (make streaming-up); volume size is listed above"
fi

section "disposable test resources"
leftover="$(docker ps -a --filter label=com.docker.compose.project=sceneops-test -q | wc -l | tr -d ' ')"
echo "  sceneops-test compose project containers: ${leftover} (a killed test run leaves them; the next run removes them)"
uv run --frozen python tests/infrastructure/disposable_env.py status 2>/dev/null \
  || echo "  disposable_env.py status unavailable (PostgreSQL / MinIO not reachable)"

section "reclaimable without touching golden state"
docker images -f dangling=true -q | wc -l | xargs printf '  dangling images: %s\n'
docker volume ls -qf dangling=true | wc -l | xargs printf '  dangling (anonymous / unreferenced) volumes: %s\n'
docker builder du 2>/dev/null | tail -1 | sed 's/^/  build cache (docker builder du): /'
echo "  stopped containers: $(docker ps -a --filter status=exited -q | wc -l | tr -d ' ')"
