from django.contrib import admin
from django.urls import include, path

# include() (not splicing urlpatterns) so django_mosaic's app_name registers
# the "mosaic:" URL namespace, mirroring how a real consumer wires it up.
urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("django_mosaic.atproto.urls")),
    path("", include("django_mosaic.urls")),
]
