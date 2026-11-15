#!/usr/bin/env bash
# Build the SvelteKit static SPA into web/build for FastAPI to serve.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}/web"
if [[ ! -d node_modules ]]; then
  npm ci
fi
npm run build
echo "Built UI → ${ROOT}/web/build (set MUSYNC_WEB_DIR or use default)"
