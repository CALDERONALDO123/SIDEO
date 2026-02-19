from django.db import migrations


def create_default_openrouter_setting(apps, schema_editor):
    AIProviderSetting = apps.get_model("cba_app", "AIProviderSetting")

    # Crea el registro por defecto si no existe.
    AIProviderSetting.objects.get_or_create(
        provider="OPENROUTER",
        defaults={
            "api_key": "",
            "model": "meta-llama/llama-3.2-3b-instruct:free",
            "timeout_seconds": 30,
        },
    )


def noop_reverse(apps, schema_editor):
    # No eliminamos configuración por seguridad.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("cba_app", "0003_aiprovidersetting"),
    ]

    operations = [
        migrations.RunPython(create_default_openrouter_setting, reverse_code=noop_reverse),
    ]
