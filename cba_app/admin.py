from __future__ import annotations

from django import forms
from django.contrib import admin

from .models import AIProviderSetting, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "avatar", "updated_at")
    search_fields = ("user__username", "user__first_name", "user__last_name")


class AIProviderSettingAdminForm(forms.ModelForm):
    api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True),
        help_text="API key del proveedor (se guarda en base de datos).",
    )

    class Meta:
        model = AIProviderSetting
        fields = "__all__"


@admin.register(AIProviderSetting)
class AIProviderSettingAdmin(admin.ModelAdmin):
    form = AIProviderSettingAdminForm

    list_display = ("provider", "masked_key", "model", "timeout_seconds", "updated_at")
    list_filter = ("provider",)
    search_fields = ("provider", "model")

    fieldsets = (
        (
            "Proveedor IA",
            {
                "fields": (
                    "provider",
                    "api_key",
                    "model",
                    "timeout_seconds",
                )
            },
        ),
    )

    def masked_key(self, obj: AIProviderSetting):
        return obj.masked_key()

    masked_key.short_description = "API key"

    def has_add_permission(self, request):
        # Para este proyecto usamos 1 config por proveedor.
        if AIProviderSetting.objects.filter(provider=AIProviderSetting.PROVIDER_OPENROUTER).exists():
            return False
        return super().has_add_permission(request)
