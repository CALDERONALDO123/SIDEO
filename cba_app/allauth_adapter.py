import logging

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings

logger = logging.getLogger(__name__)


class SideoAccountAdapter(DefaultAccountAdapter):
    def send_mail(self, template_prefix, email, context):
        try:
            return super().send_mail(template_prefix, email, context)
        except Exception:
            logger.exception(
                "Allauth email send failed (template_prefix=%s, email=%s)",
                template_prefix,
                email,
            )
            # En producción no debemos silenciar el error: si el correo no sale,
            # el usuario nunca podrá verificar su cuenta.
            if getattr(settings, "IS_PRODUCTION", False) and not getattr(
                settings, "ALLOW_NO_EMAIL_IN_PROD", False
            ):
                raise

            return None
