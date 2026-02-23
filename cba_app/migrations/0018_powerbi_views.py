"""Power BI views.

Estas VIEWS están pensadas para PostgreSQL (Render). En desarrollo local este
proyecto suele usar SQLite; allí no creamos las VIEWS para no romper `migrate`.
"""

from django.db import migrations


POSTGRES_CREATE_STATEMENTS = [
    """
    CREATE OR REPLACE VIEW vw_powerbi_resultados_cba AS
    SELECT
        proyecto AS \"PROYECTO\",
        puesto AS \"PUESTO\",
        candidato AS \"CANDIDATO\",
        costo AS \"COSTO\",
        ventaja AS \"VENTAJA\",
        costo_ventaja AS \"COSTO_VENTAJA\",
        recomendado AS \"RECOMENDADO\",
        NULL::text AS \"RESUMEN_IA\"
    FROM resultados_cba
    """,
    """
    CREATE OR REPLACE VIEW vw_powerbi_resultados_cba_recomendados AS
    SELECT
        proyecto AS \"PROYECTO\",
        puesto AS \"PUESTO\",
        candidato AS \"CANDIDATO\",
        costo AS \"COSTO\",
        ventaja AS \"VENTAJA\",
        costo_ventaja AS \"COSTO_VENTAJA\",
        recomendado AS \"RECOMENDADO\",
        resumen_ia AS \"RESUMEN_IA\"
    FROM resultados_cba_recomendados
    """,
    """
    CREATE OR REPLACE VIEW vw_powerbi_grafica_costo_ventaja AS
    SELECT
        proyectos AS \"PROYECTO\",
        puesto AS \"PUESTO\",
        candidatos AS \"CANDIDATO\",
        costo AS \"COSTO\",
        ventaja AS \"VENTAJA\",
        (costo / NULLIF(ventaja, 0)) AS \"COSTO_VENTAJA\",
        FALSE AS \"RECOMENDADO\",
        NULL::text AS \"RESUMEN_IA\"
    FROM grafica_costo_ventaja
    """,
]


POSTGRES_DROP_STATEMENTS = [
    "DROP VIEW IF EXISTS vw_powerbi_grafica_costo_ventaja",
    "DROP VIEW IF EXISTS vw_powerbi_resultados_cba_recomendados",
    "DROP VIEW IF EXISTS vw_powerbi_resultados_cba",
]


def _is_postgres(schema_editor) -> bool:
    return schema_editor.connection.vendor == "postgresql"


def create_views(apps, schema_editor):
    if not _is_postgres(schema_editor):
        return
    for stmt in POSTGRES_CREATE_STATEMENTS:
        schema_editor.execute(stmt)


def drop_views(apps, schema_editor):
    if not _is_postgres(schema_editor):
        return
    for stmt in POSTGRES_DROP_STATEMENTS:
        schema_editor.execute(stmt)


class Migration(migrations.Migration):

    dependencies = [
        ("cba_app", "0017_powerbi_setting"),
    ]

    operations = [
        migrations.RunPython(create_views, reverse_code=drop_views),
    ]
