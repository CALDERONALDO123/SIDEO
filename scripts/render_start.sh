#!/usr/bin/env bash
set -euo pipefail

echo "[render_start] Starting Django/Gunicorn"
echo "[render_start] PORT=${PORT:-}"
echo "[render_start] RENDER_EXTERNAL_HOSTNAME=${RENDER_EXTERNAL_HOSTNAME:-}"

PORT_VALUE="${PORT:-10000}"
echo "[render_start] Using bind port ${PORT_VALUE}"

echo "[render_start] Import check: cba_project.wsgi"
python -c "import cba_project.wsgi; print('wsgi import ok')"

echo "[render_start] migrate (non-fatal)"
python manage.py migrate --noinput || echo "[render_start] WARNING: migrate failed (continuing)"

echo "[render_start] Launching gunicorn (foreground)"
exec python -m gunicorn cba_project.wsgi:application --bind "0.0.0.0:${PORT_VALUE}" --access-logfile - --error-logfile - --log-level info --capture-output
