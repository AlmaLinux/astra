import code
from typing import override

from django.core.management.base import BaseCommand

from core.maintenance_shell import build_maintenance_shell_banner, build_maintenance_shell_namespace


class Command(BaseCommand):
    help = "Open a constrained maintenance shell with preloaded Astra repair helpers."

    @override
    def handle(self, *args, **options) -> None:
        code.interact(
            banner=build_maintenance_shell_banner(),
            local=build_maintenance_shell_namespace(),
        )