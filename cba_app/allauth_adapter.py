import logging

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

import requests

logger = logging.getLogger(__name__)


class SideoAccountAdapter(DefaultAccountAdapter):
    def render_mail(self, template_prefix, email, context):
        """
        Sobrescribe para forzar HTML multipart.
        Carga tanto .txt como .html y crea un mensaje multipart/alternative
        donde HTML es la parte preferida.
        """
        subject = render_to_string(f"{template_prefix}_subject.txt", context).strip()
        text_body = render_to_string(f"{template_prefix}_message.txt", context)
        
        # Intentar cargar HTML
        try:
            html_body = render_to_string(f"{template_prefix}_message.html", context)
        except Exception:
            html_body = None
        
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
        
        # Crear mensaje con HTML como alternativa preferida
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=[email],
        )
        
        # Agregar HTML como alternativa (multipart/alternative)
        if html_body:
            msg.attach_alternative(html_body, "text/html")
        
        return msg

    def send_mail(self, template_prefix, email, context):
        try:
            # Renderizamos el email con nuestro método mejorado
            msg = self.render_mail(template_prefix, email, context)

            use_sendgrid_http = bool(getattr(settings, "SENDGRID_API_KEY", "")) and bool(
                getattr(settings, "SENDGRID_USE_HTTP_API", False)
            )

            if use_sendgrid_http:
                return self._send_via_sendgrid_http(msg)

            return msg.send()
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

    def _send_via_sendgrid_http(self, msg) -> int:
        api_key = (getattr(settings, "SENDGRID_API_KEY", "") or "").strip()
        if not api_key:
            raise RuntimeError("SENDGRID_API_KEY no configurado")

        from_email = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
        if not from_email:
            raise RuntimeError("DEFAULT_FROM_EMAIL no configurado")

        to_emails = list(getattr(msg, "to", []) or [])
        if not to_emails:
            raise RuntimeError("Destinatario vacío")

        subject = (getattr(msg, "subject", "") or "").strip()
        text_body = (getattr(msg, "body", "") or "").strip()

        # Si hay alternativa HTML, la añadimos.
        html_body = None
        alternatives = getattr(msg, "alternatives", None)
        if alternatives:
            for body, mime in alternatives:
                if mime == "text/html" and body:
                    html_body = body
                    break

        contents = []
        if text_body:
            contents.append({"type": "text/plain", "value": text_body})
        if html_body:
            contents.append({"type": "text/html", "value": html_body})

        if not contents:
            contents = [{"type": "text/plain", "value": ""}]

        payload = {
            "personalizations": [{"to": [{"email": e} for e in to_emails]}],
            "from": {"email": from_email},
            "subject": subject,
            "content": contents,
        }

        timeout_seconds = int(getattr(settings, "EMAIL_TIMEOUT", 10) or 10)
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_seconds,
        )

        # SendGrid devuelve 202 Accepted cuando lo encola.
        if resp.status_code != 202:
            logger.error(
                "SendGrid API error status=%s response=%s",
                resp.status_code,
                (resp.text or "")[:1000],
            )
            resp.raise_for_status()

        return 1
