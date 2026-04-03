import re

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model


class SideoSocialAccountAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)

        email = (data.get("email") or sociallogin.account.extra_data.get("email") or "").strip().lower()
        if email and not getattr(user, "email", ""):
            user.email = email

        current_username = (getattr(user, "username", "") or "").strip()
        if current_username:
            return user

        base = "user"
        if email and "@" in email:
            base = email.split("@", 1)[0]
        base = re.sub(r"[^a-zA-Z0-9._-]", "", base).strip("._-") or "user"

        User = get_user_model()
        candidate = base
        index = 1
        while User.objects.filter(username=candidate).exists():
            index += 1
            candidate = f"{base}{index}"

        user.username = candidate
        return user
