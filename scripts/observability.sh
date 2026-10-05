#!/usr/bin/env bash
# A local Jaeger and Prometheus for the server's traces and metrics, defined in
# observability/docker-compose.yml. Loopback only. Needs Docker.
#
#   scripts/observability.sh up      start both
#   scripts/observability.sh down    stop both (metrics are kept in a volume)
#   scripts/observability.sh status
#
# Then set MESSENGER_TRACES=otlp and/or MESSENGER_METRICS=otlp in .env and
# restart the MCP client.
#
# Already run Jaeger and Prometheus some other way? Skip this script: the
# server only needs OTLP over HTTP on 127.0.0.1:4318 (traces) and
# 127.0.0.1:9090 (metrics), or wherever OTEL_EXPORTER_OTLP_*_ENDPOINT points.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/observability/docker-compose.yml")
case "${1:-}" in
  up)     "${COMPOSE[@]}" up -d && echo "Jaeger: http://localhost:16686   Prometheus: http://localhost:9090" ;;
  down)   "${COMPOSE[@]}" down ;;
  status) "${COMPOSE[@]}" ps ;;
  *)      sed -n '2,15p' "$0"; exit 2 ;;
esac
