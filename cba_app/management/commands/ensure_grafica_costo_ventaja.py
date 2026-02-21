from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Case, Count, IntegerField, Q, Sum, When

from cba_app.models import CBAResult, GraficaCostoVentaja


class Command(BaseCommand):
    help = (
        "Asegura que la tabla grafica_costo_ventaja tenga datos (backfill automático). "
        "Si está vacía o se detecta que faltan filas base 0/0 por candidato, reconstruye desde CBAResult.data_json."
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

        existing = GraficaCostoVentaja.objects.count()
        needs_rebuild = force or existing == 0

        if existing > 0 and not force:
            # Verifica que cada (proyectos, puesto, candidatos) tenga al menos 2 filas
            # y que exista su fila base 0/0. Si no, reconstruye para asegurar el formato.
            broken_groups = (
                GraficaCostoVentaja.objects.values("proyectos", "puesto", "candidatos")
                .annotate(
                    total=Count("id"),
                    zeros=Sum(
                        Case(
                            When(costo=0, ventaja=0, then=1),
                            default=0,
                            output_field=IntegerField(),
                        )
                    ),
                )
                .filter(Q(total__lt=2) | Q(zeros=0))
            )

            if broken_groups.exists():
                needs_rebuild = True

        if not needs_rebuild:
            self.stdout.write(f"OK: grafica_costo_ventaja ya tiene {existing} filas (sin cambios)")
            return

        total_results = CBAResult.objects.count()
        if total_results == 0:
            self.stdout.write("OK: no hay CBAResult para backfill (sin cambios)")
            return

        # Importa aquí para evitar circularidad y reutilizar la lógica existente.
        from django.core.management import call_command

        reason = "forzado" if force else ("vacía" if existing == 0 else "formato incompleto")
        self.stdout.write(
            f"Backfill: grafica_costo_ventaja {reason}; reconstruyendo desde {total_results} resultados..."
        )
        if limit > 0:
            call_command("rebuild_grafica_costo_ventaja", limit=limit)
        else:
            call_command("rebuild_grafica_costo_ventaja")
