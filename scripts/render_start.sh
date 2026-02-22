#!/usr/bin/env bash
set -euo pipefail

echo "[render_start] Starting Django/Gunicorn"
echo "[render_start] PORT=${PORT:-}"
echo "[render_start] RENDER_EXTERNAL_HOSTNAME=${RENDER_EXTERNAL_HOSTNAME:-}"

echo "[render_start] migrate (non-fatal)"
python manage.py migrate --noinput || echo "[render_start] WARNING: migrate failed (continuing)"

echo "[render_start] Import check: cba_project.wsgi"
python -c "import cba_project.wsgi; print('wsgi import ok')"

echo "[render_start] Launching gunicorn (background)"
python -m gunicorn cba_project.wsgi:application \
  --bind 0.0.0.0:${PORT:-8000} \
  --access-logfile - \
  --error-logfile - \
  --log-level info \
  --capture-output &
GUNICORN_PID=$!

echo "[render_start] gunicorn pid=${GUNICORN_PID}"

echo "[render_start] ensure_grafica_costo_ventaja (non-fatal)"
python manage.py ensure_grafica_costo_ventaja || echo "[render_start] WARNING: ensure_grafica_costo_ventaja failed (continuing)"

echo "[render_start] ensure_resultados_cba (non-fatal)"
python manage.py ensure_resultados_cba || echo "[render_start] WARNING: ensure_resultados_cba failed (continuing)"

echo "[render_start] ensure_superuser (non-fatal)"
python manage.py ensure_superuser || echo "[render_start] WARNING: ensure_superuser failed (continuing)"

echo "[render_start] Waiting for gunicorn"
wait ${GUNICORN_PID}
