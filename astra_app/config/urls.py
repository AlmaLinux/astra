from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django_ses.views import SESEventWebhookView

from core.views_auth import (
    FreeIPALoginView,
    otp_sync,
    password_expired,
    password_reset_confirm,
    password_reset_request,
)

urlpatterns = [
    path('ses/event-webhook/', SESEventWebhookView.as_view(), name='event_webhook'),
    path('register/', include('core.urls_registration')),
    path('login/', FreeIPALoginView.as_view(), name='login'),
    path('otp/sync/', otp_sync, name='otp-sync'),
    path('password-reset/', password_reset_request, name='password-reset'),
    path('password-reset/confirm/', password_reset_confirm, name='password-reset-confirm'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('password-expired/', password_expired, name='password-expired'),
    path('admin/django-ses/', include('django_ses.urls')),
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
]

if settings.DEBUG:
    from django.conf.urls.static import static

    from core.debug_views import cache_debug_view, sankey_debug_view

    urlpatterns += [
        path('__debug__/cache/', cache_debug_view, name='cache-debug'),
        path('__debug__/sankey/', sankey_debug_view, name='sankey-debug'),
    ]

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
