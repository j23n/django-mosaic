from django.urls import path, include
from django.contrib import admin
from django_mosaic.urls import urlpatterns as mosaic_urls

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("django_mosaic.atproto.urls")),
] + mosaic_urls
