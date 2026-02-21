import logging

from allauth.account.adapter import DefaultAccountAdapter

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
            return
