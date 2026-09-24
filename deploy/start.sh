#!/bin/sh
# Single-container entrypoint for Azure Container Apps: run the conversion worker
# in the background (restarting it if it exits) and the API in the foreground.
set -e
( while true; do python -m app.worker || true; sleep 2; done ) &
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log --proxy-headers --forwarded-allow-ips '*'
