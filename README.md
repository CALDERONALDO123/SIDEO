# Sistema CBA (Choosing By Advantages)

Proyecto Django para implementar un sistema automatizado de apoyo a la decisión usando la metodología Choosing By Advantages (CBA), con un flujo guiado de 10 pasos para comparar alternativas en base a sus ventajas.

## Requisitos

- Python 3.12 (en un entorno virtual .venv)
- Django (ya instalado en este entorno)

## Cómo ejecutar

1. Activar el entorno virtual si aún no está activo.
2. Ejecutar las migraciones iniciales:
   - `python manage.py migrate`
3. Levantar el servidor de desarrollo:
   - `python manage.py runserver`

Luego acceder a `http://127.0.0.1:8000/` en el navegador.

## Asistente IA (OpenRouter)

Este proyecto incluye un "Asistente IA" en el Dashboard del análisis CBA que genera un resumen técnico de decisión.

Configuración (variables de entorno):

- `DJANGO_SECRET_KEY`: secret key de Django (obligatoria en producción).
- `DJANGO_DEBUG`: `true/false` (en producción debe ser `false`).
- `DJANGO_ALLOWED_HOSTS`: hosts permitidos separados por comas.

- `OPENROUTER_API_KEY`: API key de OpenRouter (obligatoria).
- `OPENROUTER_MODEL`: modelo a usar (opcional). Por defecto: `meta-llama/llama-3.1-8b-instruct:free`.
- `OPENROUTER_TIMEOUT_SECONDS`: timeout en segundos (opcional). Por defecto: `30`.

Si no se configura `OPENROUTER_API_KEY`, el botón del asistente mostrará un mensaje de error indicando la falta de configuración.
