import json
from typing import override

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError

from core.account_deletion import invalidate_sessions_for_freeipa_username
from core.freeipa.client import clear_freeipa_service_client_cache
from core.freeipa.e2e_registry import E2E_FREEIPA_USERNAMES, is_e2e_fake_freeipa_enabled, reset_e2e_fake_freeipa_state
from core.freeipa.utils import _user_cache_key


class Command(BaseCommand):
    help = "Reset the minimal auth-profile E2E scenario state."

    @override
    def handle(self, *args, **options) -> None:
        del args, options

        if not is_e2e_fake_freeipa_enabled():
            raise CommandError(
                "auth_profile_reset requires ASTRA_E2E_MODE=True and ASTRA_E2E_FAKE_FREEIPA_ENABLED=True."
            )

        for username in E2E_FREEIPA_USERNAMES:
            invalidate_sessions_for_freeipa_username(username)
            cache.delete(_user_cache_key(username))

        reset_e2e_fake_freeipa_state()
        clear_freeipa_service_client_cache()

        self.stdout.write(json.dumps({"scenario": "auth-profile", "status": "reset"}))