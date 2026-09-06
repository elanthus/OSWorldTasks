#!/bin/bash
set -euo pipefail

PIXELGYM_BIND_ADDRESS="${PIXELGYM_BIND_ADDRESS:-127.0.0.1}"
export PIXELGYM_BIND_ADDRESS

exec uvicorn pixelgym.platform.bootstrap:create_app --factory \
  --host "$PIXELGYM_BIND_ADDRESS" \
  --port 8000 \
  --no-proxy-headers
