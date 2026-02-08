import logging


class HealthEndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "/healthz" in message or "/readyz" in message:
            return " 200 " not in message
        return True