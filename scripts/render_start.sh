#!/usr/bin/env bash
set -euo pipefail

echo "[render_start] Starting Django/Gunicorn"
echo "[render_start] PORT=${PORT:-}"
echo "[render_start] RENDER_EXTERNAL_HOSTNAME=${RENDER_EXTERNAL_HOSTNAME:-}"

PORT_VALUE="${PORT:-10000}"
TIMEOUT_VALUE="${GUNICORN_TIMEOUT:-180}"
GRACEFUL_TIMEOUT_VALUE="${GUNICORN_GRACEFUL_TIMEOUT:-180}"
WORKERS_VALUE="${WEB_CONCURRENCY:-1}"
echo "[render_start] Using bind port ${PORT_VALUE}"
echo "[render_start] Gunicorn timeout=${TIMEOUT_VALUE}s graceful=${GRACEFUL_TIMEOUT_VALUE}s workers=${WORKERS_VALUE}"

# Evita warning de middleware en runtime cuando el directorio aún no existe.
mkdir -p staticfiles

echo "[render_start] migrate (non-fatal)"
python manage.py migrate --noinput || echo "[render_start] WARNING: migrate failed (continuing)"

echo "[render_start] Launching gunicorn (foreground)"
exec python -m gunicorn cba_project.wsgi:application \
	--bind "0.0.0.0:${PORT_VALUE}" \
	--workers "${WORKERS_VALUE}" \
	--timeout "${TIMEOUT_VALUE}" \
	--graceful-timeout "${GRACEFUL_TIMEOUT_VALUE}" \
	--access-logfile - \
	--error-logfile - \
	--log-level info \
	--capture-output
