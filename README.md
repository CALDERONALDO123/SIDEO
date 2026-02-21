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

## Despliegue en Render

Este repo incluye `render.yaml` para desplegar como servicio web en Render.

Notas importantes:

- El arranque en producción debe escuchar el puerto que Render expone en `PORT` (por ejemplo con Gunicorn `--bind 0.0.0.0:$PORT`). Si no, Render suele mostrar `HTTP ERROR 502`.
- Variables mínimas: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`. Render suele proveer `RENDER_EXTERNAL_HOSTNAME` automáticamente.

### Email (SendGrid) + Verificación

En producción (Render) el proyecto asume que el registro envía correo de verificación. Para eso debes configurar un proveedor SMTP.

Opción recomendada: SendGrid. Define estas variables en Render:

- `SENDGRID_API_KEY`: API Key de SendGrid (Mail Send → Full Access)
- `DEFAULT_FROM_EMAIL`: el correo verificado en SendGrid (Single Sender)

Notas:

- Si NO configuras `SENDGRID_API_KEY` ni `DJANGO_EMAIL_HOST`, el deploy en producción fallará con un error explícito (para evitar un sitio "funcionando" sin correo). Si realmente quieres permitir producción sin correo, setea `ALLOW_NO_EMAIL_IN_PROD=true` (no recomendado).
- Con `SENDGRID_API_KEY` configurado, por defecto el proyecto envía correos vía API HTTP de SendGrid (no SMTP) para evitar bloqueos/timeouts de SMTP en PaaS. Puedes forzar el comportamiento con `SENDGRID_USE_HTTP_API=true/false`.

Alternativa SMTP genérica (si no usas SendGrid):

- `DJANGO_EMAIL_HOST`
- `DJANGO_EMAIL_PORT` (por defecto `587`)
- `DJANGO_EMAIL_HOST_USER`
- `DJANGO_EMAIL_HOST_PASSWORD`
- `DJANGO_EMAIL_USE_TLS` (`true/false`, por defecto `true`)
- `DEFAULT_FROM_EMAIL`

### Superusuario sin Shell (Render Free)

Si tu plan no permite abrir Shell, puedes crear el superusuario automáticamente configurando estas variables de entorno y redeployando:

- `DJANGO_SUPERUSER_USERNAME`
- `DJANGO_SUPERUSER_PASSWORD`
- `DJANGO_SUPERUSER_EMAIL` (opcional)

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
