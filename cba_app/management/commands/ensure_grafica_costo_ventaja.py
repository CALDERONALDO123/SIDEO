from __future__ import annotations

from django.core.management.base import BaseCommand

from cba_app.models import CBAResult, GraficaCostoVentaja


class Command(BaseCommand):
    help = (
        "Asegura que la tabla grafica_costo_ventaja tenga datos (backfill automático). "
        "Si está vacía, reconstruye desde CBAResult.data_json."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Procesa solo los últimos N resultados (0 = todos).",
        )

    def handle(self, *args, **options):
        limit = int(options.get("limit") or 0)

        existing = GraficaCostoVentaja.objects.count()
        if existing > 0:
            self.stdout.write(f"OK: grafica_costo_ventaja ya tiene {existing} filas (sin cambios)")
            return

        total_results = CBAResult.objects.count()
        if total_results == 0:
            self.stdout.write("OK: no hay CBAResult para backfill (sin cambios)")
            return

        # Importa aquí para evitar circularidad y reutilizar la lógica existente.
        from django.core.management import call_command

        self.stdout.write(
            f"Backfill: grafica_costo_ventaja vacía; reconstruyendo desde {total_results} resultados..."
        )
        if limit > 0:
            call_command("rebuild_grafica_costo_ventaja", limit=limit)
        else:
            call_command("rebuild_grafica_costo_ventaja")
