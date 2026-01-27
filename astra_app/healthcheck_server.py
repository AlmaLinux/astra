#!/usr/bin/env python3
"""Deprecated.

Health checks are now served from the Django app on /healthz and /readyz.
This module is intentionally empty to discourage reuse.
"""

raise SystemExit("healthcheck_server.py is deprecated; use Django /healthz and /readyz")
