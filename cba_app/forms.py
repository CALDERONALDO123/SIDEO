from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import UserProfile
from .models import Alternative, Criterion


class AlternativeForm(forms.ModelForm):
    class Meta:
        model = Alternative
        # En el Paso 1 solo se captura el nombre del postor
        fields = ["name"]
        labels = {
            "name": "Nombre del postor (alternativa)",
        }
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control form-control-sm",
                "autocomplete": "off",
            }),
        }


class CriterionForm(forms.ModelForm):
    class Meta:
        model = Criterion
        # En el Paso 2 solo se captura el nombre del factor
        fields = ["name"]
        labels = {
            "name": "Nombre del factor de decisión",
        }
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control form-control-sm",
                "autocomplete": "off",
            }),
        }


class CBASetupForm(forms.Form):
    SECTOR_PUBLICO = "PUBLICO"
    SECTOR_PRIVADO = "PRIVADO"

    SECTOR_CHOICES = [
        (SECTOR_PUBLICO, "Público"),
        (SECTOR_PRIVADO, "Privado"),
    ]

    sector = forms.ChoiceField(
        label="Tipo de proyecto",
        choices=SECTOR_CHOICES,
        widget=forms.RadioSelect,
    )

    project_name = forms.CharField(
        label="Nombre del proyecto",
        max_length=200,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej.: Mejoramiento de infraestructura / Servicio / Implementación...",
            "class": "form-control",
            "autocomplete": "off",
        }),
    )

    location = forms.CharField(
        label="Lugar (región / provincia / distrito)",
        max_length=200,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej.: Cusco / Cusco / Santiago",
            "class": "form-control",
            "autocomplete": "off",
        }),
    )

    requesting_area = forms.CharField(
        label="Área solicitante",
        max_length=200,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej.: Oficina de Logística / TI / Operaciones...",
            "class": "form-control",
            "autocomplete": "off",
        }),
    )

    reference_budget = forms.CharField(
        label="Presupuesto referencial (opcional)",
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej.: S/ 250,000",
            "class": "form-control",
            "autocomplete": "off",
        }),
    )

    objective = forms.CharField(
        label="Objetivo de la selección",
        widget=forms.Textarea(attrs={
            "rows": 3,
            "placeholder": "Describe qué se busca lograr con esta selección (alcance, necesidad, resultado esperado).",
            "class": "form-control",
        }),
    )

    public_entity = forms.CharField(
        label="Entidad pública",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej.: Gobierno Regional / Municipalidad / Ministerio...",
            "class": "form-control",
            "autocomplete": "off",
        }),
    )

    private_company = forms.CharField(
        label="Empresa / área",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Ej.: Empresa ABC / Gerencia de Operaciones...",
            "class": "form-control",
            "autocomplete": "off",
        }),
    )

    def clean(self):
        cleaned = super().clean()
        sector = cleaned.get("sector")
        public_entity = (cleaned.get("public_entity") or "").strip()
        private_company = (cleaned.get("private_company") or "").strip()

        if sector == self.SECTOR_PUBLICO and not public_entity:
            self.add_error("public_entity", "Indica la entidad pública.")

        if sector == self.SECTOR_PRIVADO and not private_company:
            self.add_error("private_company", "Indica la empresa o área.")

        return cleaned


class GuidePdfUploadForm(forms.Form):
    pdf_file = forms.FileField(
        label="PDF",
        help_text="Solo archivos .pdf",
        widget=forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
    )

    def clean_pdf_file(self):
        uploaded = self.cleaned_data["pdf_file"]
        name = (uploaded.name or "").lower()
        if not name.endswith(".pdf"):
            raise forms.ValidationError("El archivo debe ser un PDF (.pdf).")
        return uploaded


class GuideShareLinkForm(forms.Form):
    title = forms.CharField(
        label="Título (opcional)",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Título (opcional)",
                "class": "cba-input",
                "autocomplete": "off",
            }
        ),
    )

    subtitle = forms.CharField(
        label="Subtítulo (opcional)",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Subtítulo (opcional)",
                "class": "cba-input",
                "autocomplete": "off",
            }
        ),
    )

    password = forms.CharField(
        label="Contraseña (opcional)",
        required=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Contraseña (opcional)",
                "class": "cba-input",
                "autocomplete": "new-password",
            },
            render_value=False,
        ),
    )


class GuideSharedPasswordForm(forms.Form):
    password = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Ingresa la contraseña",
                "class": "cba-input",
                "autocomplete": "current-password",
            },
            render_value=False,
        ),
    )


class SignUpForm(UserCreationForm):
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

    class Meta:
        model = User
        fields = ("first_name", "last_name", "username", "password1", "password2")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = (self.cleaned_data.get("first_name") or "").strip()
        user.last_name = (self.cleaned_data.get("last_name") or "").strip()
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name")
        labels = {
            "first_name": "Nombre",
            "last_name": "Apellido",
        }
        widgets = {
            "first_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Tu nombre",
                    "autocomplete": "given-name",
                }
            ),
            "last_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Tu apellido",
                    "autocomplete": "family-name",
                }
            ),
        }


class ProfilePhotoForm(forms.ModelForm):
    delete_avatar = forms.BooleanField(
        required=False,
        label="Eliminar foto actual",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = UserProfile
        fields = ("avatar",)
        labels = {
            "avatar": "Cambiar foto",
        }
        widgets = {
            "avatar": forms.FileInput(
                attrs={
                    "accept": "image/*",
                    "class": "d-none",
                }
            ),
        }
