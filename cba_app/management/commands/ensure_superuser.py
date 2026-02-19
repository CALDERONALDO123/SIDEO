import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Crea (o promueve) un superusuario en base a variables de entorno. "
        "Pensado para despliegues sin acceso a 'Shell' (por ejemplo Render free)."
    )

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "").strip()
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()

        if not username or not password:
            self.stdout.write(
                "ensure_superuser: variables no configuradas; omitiendo. "
                "(Requiere DJANGO_SUPERUSER_USERNAME y DJANGO_SUPERUSER_PASSWORD)"
            )
            return

        User = get_user_model()

        user = User.objects.filter(username=username).first()
        if user is None:
            User.objects.create_superuser(username=username, email=email, password=password)
            self.stdout.write(self.style.SUCCESS(f"Superusuario creado: {username}"))
            return

        changed = False
        if not getattr(user, "is_staff", False):
            user.is_staff = True
            changed = True
        if not getattr(user, "is_superuser", False):
            user.is_superuser = True
            changed = True

        if changed:
            user.set_password(password)
            user.email = email or getattr(user, "email", "")
            user.save(update_fields=["is_staff", "is_superuser", "password", "email"])
            self.stdout.write(self.style.SUCCESS(f"Usuario promovido a superusuario: {username}"))
        else:
            self.stdout.write(f"Superusuario ya existe: {username} (sin cambios)")
