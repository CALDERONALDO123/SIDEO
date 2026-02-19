from django.db import models
from django.contrib.auth.models import User


def _user_avatar_upload_to(instance, filename: str) -> str:
    base = f"avatars/user_{instance.user_id}"
    filename = (filename or "avatar").replace("\\", "/").split("/")[-1]
    return f"{base}/{filename}"


class Criterion(models.Model):
    """Paso 2 y 3: factores de decisión y tipo de criterio."""

    TYPE_MUST = "MUST"
    TYPE_WANT = "WANT"

    TYPE_CHOICES = [
        (TYPE_MUST, "Debe tener"),
        (TYPE_WANT, "Desea tener"),
    ]

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    criterion_type = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
        default=TYPE_MUST,
        help_text="Indica si el factor es 'Debe tener' o 'Desea tener'.",
    )

    def __str__(self):
        return self.name


class Alternative(models.Model):
    """Paso 1 y 10: alternativas y su costo."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Costo de la alternativa (para evaluar costo versus ventaja).",
    )

    def __str__(self):
        return self.name


class Attribute(models.Model):
    """Paso 4 y 5: atributos de cada alternativa por factor y atributo menos preferido."""

    criterion = models.ForeignKey(Criterion, on_delete=models.CASCADE, related_name="attributes")
    alternative = models.ForeignKey(Alternative, on_delete=models.CASCADE, related_name="attributes")
    description = models.TextField(help_text="Descripción del atributo de esta alternativa para este factor.")
    is_least_preferred = models.BooleanField(
        default=False,
        help_text="Marca si este es el atributo menos preferido para este factor.",
    )

    def __str__(self):
        return f"Atributo de {self.alternative} en {self.criterion}"


class Advantage(models.Model):
    """Paso 6 a 9: ventajas e importancia de cada alternativa."""

    criterion = models.ForeignKey(Criterion, on_delete=models.CASCADE, related_name="advantages")
    alternative = models.ForeignKey(Alternative, on_delete=models.CASCADE, related_name="advantages")
    description = models.TextField(help_text="Descripción de la ventaja respecto al atributo menos preferido.")
    importance = models.PositiveIntegerField(help_text="Puntaje de importancia asignado a esta ventaja.")
    is_main = models.BooleanField(
        default=False,
        help_text="Marca si esta es la ventaja principal para esta alternativa.",
    )

    def __str__(self):
        return f"{self.criterion} - {self.alternative} ({self.importance})"


class CBAResult(models.Model):
    """Resumen de un análisis completo CBA (Paso 10)."""

    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    winner_name = models.CharField(max_length=200, blank=True)
    winner_total = models.PositiveIntegerField(default=0)
    winner_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    winner_ratio = models.FloatField(null=True, blank=True)

    power_bi_url = models.URLField(
        blank=True,
        help_text="URL del dashboard/reporte en Power BI para este proyecto (opcional).",
    )

    data_json = models.TextField(blank=True, help_text="Datos de costo y total de ventajas usados en el gráfico.")
    summary_text = models.TextField(
        blank=True,
        help_text="Resumen IA del asistente de decisión (se congela al guardar el Paso 10).",
    )
    inconsistency_text = models.TextField(
        blank=True,
        help_text="Reporte IA de inconsistencias (Paso 10) almacenado junto al resultado.",
    )

    def __str__(self):
        return f"{self.name} - {self.winner_name}"


class AIProviderSetting(models.Model):
    """Configuración de proveedores IA administrable desde Django Admin."""

    PROVIDER_OPENROUTER = "OPENROUTER"
    PROVIDER_CHOICES = [
        (PROVIDER_OPENROUTER, "OpenRouter"),
    ]

    provider = models.CharField(
        max_length=50,
        choices=PROVIDER_CHOICES,
        unique=True,
        default=PROVIDER_OPENROUTER,
    )
    api_key = models.CharField(
        max_length=255,
        blank=True,
        help_text="Pega aquí tu API key del proveedor (se recomienda no exponerla fuera del Admin).",
    )
    model = models.CharField(
        max_length=255,
        blank=True,
        help_text="Id del modelo en OpenRouter (ej. meta-llama/llama-3.2-3b-instruct:free)",
    )
    timeout_seconds = models.FloatField(default=30)
    updated_at = models.DateTimeField(auto_now=True)

    def masked_key(self):
        if not self.api_key:
            return "(vacía)"
        return f"***{self.api_key[-4:]}"

    def __str__(self):
        return f"{self.get_provider_display()} ({self.masked_key()})"

    class Meta:
        verbose_name = "Configuración de IA"
        verbose_name_plural = "Configuraciones de IA"


class SharedGuideLink(models.Model):
    """Link compartible para la Guía PDF con contraseña opcional."""

    token = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=200, blank=True)
    subtitle = models.CharField(max_length=200, blank=True)
    password_hash = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def requires_password(self) -> bool:
        return bool(self.password_hash)

    def __str__(self):
        display = self.title.strip() or "Guía"
        return f"{display} ({self.token})"

    class Meta:
        verbose_name = "Link compartido de guía"
        verbose_name_plural = "Links compartidos de guía"


class UserProfile(models.Model):
    """Perfil de usuario (foto/avatar)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    avatar = models.FileField(upload_to=_user_avatar_upload_to, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Perfil de {self.user.username}"

    class Meta:
        verbose_name = "Perfil de usuario"
        verbose_name_plural = "Perfiles de usuario"
