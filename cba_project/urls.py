"""
URL configuration for cba_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static


def _using_cloudinary_storage() -> bool:
    backend = getattr(settings, "DEFAULT_FILE_STORAGE", "") or ""
    if backend == "cloudinary_storage.storage.MediaCloudinaryStorage":
        return True

    storages = getattr(settings, "STORAGES", None)
    if isinstance(storages, dict):
        default = storages.get("default")
        if isinstance(default, dict):
            be = (default.get("BACKEND") or "").strip()
            if be == "cloudinary_storage.storage.MediaCloudinaryStorage":
                return True

    return False

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('cba_app.urls')),
]

if settings.DEBUG or not _using_cloudinary_storage():
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
