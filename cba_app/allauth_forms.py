from __future__ import annotations

from allauth.account import app_settings as allauth_app_settings
from allauth.account.adapter import get_adapter
from allauth.account.forms import LoginForm, SignupForm
from allauth.account.internal import flows
from allauth.account.models import Login
from allauth.core import context
from django import forms


class AllauthSignupForm(SignupForm):
    first_name = forms.CharField(
        label="Nombre",
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Tu nombre",
                "class": "cba-input",
                "autocomplete": "given-name",
            }
        ),
    )
    last_name = forms.CharField(
        label="Apellido",
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Tu apellido",
                "class": "cba-input",
                "autocomplete": "family-name",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        email_field = self.fields.get("email")
        if email_field is not None:
            email_field.label = "Correo electrónico"
            email_field.widget.attrs.setdefault("placeholder", "nombre@dominio.com")
            email_field.help_text = (
                "Usa un correo válido. Ej: nombre@dominio.com"
            )

        username_field = self.fields.get("username")
        if username_field is not None:
            username_field.label = "Usuario"
            username_field.widget.attrs.setdefault("placeholder", "Tu usuario")

    def save(self, request):
        user = super().save(request)
        user.first_name = (self.cleaned_data.get("first_name") or "").strip()
        user.last_name = (self.cleaned_data.get("last_name") or "").strip()
        user.save(update_fields=["first_name", "last_name"])
        return user


class AllauthLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        login_field = self.fields.get("login")
        if login_field is not None:
            login_field.label = "Usuario o correo"
            login_field.widget.attrs.setdefault("placeholder", "Tu usuario o correo")
            login_field.help_text = "Puedes ingresar con tu usuario o con tu correo."

        password_field = self.fields.get("password")
        if password_field is not None:
            password_field.label = "Contraseña"

        remember_field = self.fields.get("remember")
        if remember_field is not None:
            remember_field.label = "Recordarme"

    def _clean_with_password(self, credentials: dict):
        adapter = get_adapter(self.request)
        user = adapter.authenticate(self.request, **credentials)
        if user:
            email_verification = None
            if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
                email_verification = allauth_app_settings.EmailVerificationMethod.OPTIONAL

            login = Login(
                user=user,
                email=credentials.get("email"),
                email_verification=email_verification,
            )
            if flows.login.is_login_rate_limited(context.request, login):
                raise adapter.validation_error("too_many_login_attempts")
            self._login = login
            self.user = user  # type: ignore
        else:
            login_method = flows.login.derive_login_method(login=self.cleaned_data["login"])
            raise adapter.validation_error(f"{login_method.value}_password_mismatch")
        return self.cleaned_data
