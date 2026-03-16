import logging
import os

from django.core.wsgi import get_wsgi_application

from core.logging_extras import current_exception_log_fields

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()

logger = logging.getLogger(__name__)

try:
    from core.startup import ensure_membership_type_groups_exist

    ensure_membership_type_groups_exist()
    from core.systemd_notify import send_systemd_notification

    try:
        send_systemd_notification("READY=1")
    except Exception:
        logger.exception("Systemd notification failed", extra=current_exception_log_fields())
except Exception:
    logger.exception("Startup membership group sync failed", extra=current_exception_log_fields())
    raise
