from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.utils import timezone

from cba_app.models import CBAResult, GraficaCostoVentaja


class Command(BaseCommand):
    help = (
        "Reconstruye la tabla grafica_costo_ventaja desde CBAResult.data_json "
        "(genera 2 filas por candidato: 0/0 y valor)."
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
            GraficaCostoVentaja.objects.all().delete()

        qs = CBAResult.objects.order_by("-created_at")
        if limit > 0:
            qs = qs[:limit]

        created = 0

        def to_decimal(value):
            if value is None:
                return Decimal("0")
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError):
                return Decimal("0")

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
            for item in dashboard_payload or []:
                if not isinstance(item, dict):
                    continue

                candidato = (item.get("name") or "").strip()
                if not candidato:
                    continue
                candidato = candidato[:150]

                costo = to_decimal(item.get("cost"))
                ventaja = to_decimal(item.get("total"))

                rows.append(
                    GraficaCostoVentaja(
                        proyectos=proyecto,
                        puesto=puesto,
                        candidatos=candidato,
                        costo=Decimal("0"),
                        ventaja=Decimal("0"),
                    )
                )
                rows.append(
                    GraficaCostoVentaja(
                        proyectos=proyecto,
                        puesto=puesto,
                        candidatos=candidato,
                        costo=costo,
                        ventaja=ventaja,
                    )
                )

            if rows:
                GraficaCostoVentaja.objects.bulk_create(rows, batch_size=500)
                created += len(rows)

        self.stdout.write(self.style.SUCCESS(f"OK: {created} filas creadas"))
