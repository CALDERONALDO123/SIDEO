from __future__ import annotations

from django.core.management.base import BaseCommand

from cba_app.models import CBAResult, ResultadoCBA


class Command(BaseCommand):
    help = (
        "Asegura que la tabla resultados_cba tenga datos correctos (backfill automático). "
        "Si está vacía o hay filas legacy sin result_id, reconstruye desde CBAResult.data_json."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Procesa solo los últimos N resultados (0 = todos).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Fuerza la reconstrucción incluso si ya hay filas.",
        )

    def handle(self, *args, **options):
        limit = int(options.get("limit") or 0)
        force = bool(options.get("force"))

        existing = ResultadoCBA.objects.count()
        legacy = False
        if existing > 0:
            legacy = ResultadoCBA.objects.filter(result__isnull=True).exists()

        if not force and existing > 0 and not legacy:
            self.stdout.write(f"OK: resultados_cba ya tiene {existing} filas (sin cambios)")
            return

        total_results = CBAResult.objects.count()
        if total_results == 0:
            self.stdout.write("OK: no hay CBAResult para backfill (sin cambios)")
            return

        from django.core.management import call_command

        reason = "forzado" if force else ("vacía" if existing == 0 else "legacy sin result_id")
        self.stdout.write(
            f"Backfill: resultados_cba {reason}; reconstruyendo desde {total_results} resultados..."
        )

        if limit > 0:
            call_command("rebuild_resultados_cba", limit=limit)
        else:
            call_command("rebuild_resultados_cba")
