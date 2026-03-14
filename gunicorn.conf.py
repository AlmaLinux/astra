from __future__ import annotations

accesslog = None
errorlog = "-"
timeout = 120  # 2 minutes; during imports
capture_output = True
loglevel = "info"
forwarded_allow_ips = "*"
access_log_format = '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "health_endpoint": {
            "()": "config.logging_filters.HealthEndpointFilter",
        },
        "hetrix_access": {
            "()": "config.logging_filters.HetrixAccessFilter",
        },
    },
    "formatters": {
        "access": {
            "format": "%(message)s",
        },
        "error": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "access",
        },
        "stderr": {
            "class": "logging.StreamHandler",
            "formatter": "error",
        },
    },
    "loggers": {
        "gunicorn.error": {
            "handlers": ["stderr"],
            "level": "INFO",
            "propagate": False,
        },
        "gunicorn.access": {
            "handlers": ["stdout"],
            "level": "WARNING",
            "filters": ["health_endpoint", "hetrix_access"],
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["stderr"],
        "level": "INFO",
    },
}