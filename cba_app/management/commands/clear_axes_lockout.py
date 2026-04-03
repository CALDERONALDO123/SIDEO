from django.core.management.base import BaseCommand
from axes.models import AccessAttempt, AxisAttempt


class Command(BaseCommand):
    help = "Limpia los registros de intentos fallidos de django-axes"

    def handle(self, *args, **options):
        try:
            deleted_count, _ = AccessAttempt.objects.all().delete()
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Se limpiaron {deleted_count} registros de AccessAttempt"
                )
            )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"AccessAttempt: {e}"))

        try:
            deleted_count, _ = AxisAttempt.objects.all().delete()
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Se limpiaron {deleted_count} registros de AxisAttempt"
                )
            )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"AxisAttempt: {e}"))

        self.stdout.write(
            self.style.SUCCESS(
                "✓ Bloqueo de django-axes limpiado correctamente"
            )
        )
