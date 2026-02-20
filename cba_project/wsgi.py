"""
WSGI config for cba_project project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.management import call_command
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cba_project.settings')


def _env_true(name: str, default: bool = False) -> bool:
	raw = os.environ.get(name)
	if raw is None:
		return default
	return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _run_startup_tasks_if_needed() -> None:
	already_done = os.environ.get("CBA_STARTUP_TASKS_DONE") == "1"
	if already_done:
		return

	run_forced = _env_true("DJANGO_RUN_STARTUP_TASKS", False)
	is_render_runtime = bool(os.environ.get("RENDER_EXTERNAL_HOSTNAME"))

	if not (run_forced or is_render_runtime):
		return

	os.environ["CBA_STARTUP_TASKS_DONE"] = "1"

	call_command("migrate", interactive=False, verbosity=0)
	call_command("ensure_superuser", verbosity=0)


_run_startup_tasks_if_needed()

application = get_wsgi_application()
