from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.utils import timezone

from cba_app.models import CBAResult, ResultadoCBA


class Command(BaseCommand):
    help = (
        "Reconstruye la tabla resultados_cba desde CBAResult.data_json "
        "(1 fila por candidato por resultado)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--append",
            action="store_true",
            help="No borra la tabla antes de cargar (puede duplicar filas).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Procesa solo los últimos N resultados (0 = todos).",
        )

    def handle(self, *args, **options):
        append = bool(options.get("append"))
        limit = int(options.get("limit") or 0)

        if not append:
            ResultadoCBA.objects.all().delete()

        qs = CBAResult.objects.order_by("-created_at")
        if limit > 0:
            qs = qs[:limit]

        created = 0

        def to_decimal(value):
            if value is None:
                return None
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError):
                return None

        for result in qs:
            try:
                payload = json.loads(result.data_json or "{}")
            except json.JSONDecodeError:
                payload = {}

            setup = None
            dashboard_payload = []

            if isinstance(payload, dict):
                setup = payload.get("setup")
                dashboard_payload = payload.get("dashboard") or payload.get("chart_data") or []
            elif isinstance(payload, list):
                dashboard_payload = payload

            proyecto = None
            puesto = None
            if isinstance(setup, dict):
                proyecto = (setup.get("project_name") or "").strip() or None
                puesto = (setup.get("requesting_area") or "").strip() or None

            proyecto = (proyecto or result.name or "").strip()[:255]
            if puesto:
                puesto = puesto[:150]

            rows = []
            winner = (result.winner_name or "").strip()

            for item in dashboard_payload or []:
                if not isinstance(item, dict):
                    continue

                candidato = (item.get("name") or "").strip()
                if not candidato:
                    continue
                candidato = candidato[:150]

                costo = to_decimal(item.get("cost"))
                ventaja = to_decimal(item.get("total"))
                ratio = to_decimal(item.get("ratio"))

                rows.append(
                    ResultadoCBA(
                        result=result,
                        proyecto=proyecto,
                        puesto=puesto,
                        candidato=candidato,
                        costo=costo,
                        ventaja=ventaja,
                        costo_ventaja=ratio,
                        recomendado=bool(winner and candidato == winner),
                        fecha=result.created_at or timezone.now(),
                    )
                )

            if rows:
                ResultadoCBA.objects.bulk_create(rows, batch_size=500)
                created += len(rows)

        self.stdout.write(self.style.SUCCESS(f"OK: {created} filas creadas"))
