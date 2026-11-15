#!/usr/bin/env bash
# Smoke-check a running MuSync hub (Pi, Uno Q, or laptop mock).
set -euo pipefail
BASE="${1:-http://127.0.0.1:8080}"

echo "GET ${BASE}/api/health"
curl -fsS "${BASE}/api/health" | python3 -m json.tool

echo
echo "GET ${BASE}/api/devices"
curl -fsS "${BASE}/api/devices" | python3 -m json.tool

echo
echo "GET ${BASE}/api/transport"
curl -fsS "${BASE}/api/transport" | python3 -m json.tool

echo
echo "OK — hub API reachable at ${BASE}"
