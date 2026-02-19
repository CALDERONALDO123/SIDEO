from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cba_app", "0007_cbaresult_power_bi_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="cbaresult",
            name="summary_text",
            field=models.TextField(
                blank=True,
                help_text="Resumen IA del asistente de decisión (se congela al guardar el Paso 10).",
            ),
        ),
        migrations.AddField(
            model_name="cbaresult",
            name="inconsistency_text",
            field=models.TextField(
                blank=True,
                help_text="Reporte IA de inconsistencias (Paso 10) almacenado junto al resultado.",
            ),
        ),
    ]
