from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cba_app", "0008_cbaresult_ai_texts"),
    ]

    operations = [
        migrations.AddField(
            model_name="sharedguidelink",
            name="subtitle",
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
