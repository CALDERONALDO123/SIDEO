from __future__ import annotations

from allauth.account.forms import SignupForm
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

    def save(self, request):
        user = super().save(request)
        user.first_name = (self.cleaned_data.get("first_name") or "").strip()
        user.last_name = (self.cleaned_data.get("last_name") or "").strip()
        user.save(update_fields=["first_name", "last_name"])
        return user
