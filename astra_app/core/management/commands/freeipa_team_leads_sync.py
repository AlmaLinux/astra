import logging
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.freeipa.group import sync_materialized_team_leads_group

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync the configured materialized FreeIPA team-leads group from the configured source group's direct child-group managers."

    def _write_and_log(self, message: str) -> None:
        self.stdout.write(message)
        logger.info(message)

    def _write_report(self, *, report: dict[str, object]) -> None:
        prefix = "[dry-run] " if bool(report["dry_run"]) else ""
        source_group_cn = str(report["source_group_cn"])
        destination_group_cn = str(report["destination_group_cn"])
        self._write_and_log(
            f"{prefix}Team-leads sync {source_group_cn} -> {destination_group_cn}"
        )

        if bool(report["create_destination"]):
            self._write_and_log(f"{prefix}Created destination group {destination_group_cn}.")

        sections = (
            ("add_members", "Adding", "user(s)"),
            ("remove_members", "Removing", "direct user(s)"),
            ("remove_member_groups", "Removing", "nested group(s)"),
            ("remove_sponsors", "Removing", "sponsor user(s)"),
            ("remove_sponsor_groups", "Removing", "sponsor group(s)"),
        )
        changed = False
        for key, verb, noun in sections:
            values = [str(value) for value in report[key]]
            if not values:
                continue
            changed = True
            joined_values = ", ".join(values)
            self._write_and_log(f"{verb} {len(values)} {noun}: {joined_values}")

        if not changed and not bool(report["create_destination"]):
            self._write_and_log(f"{prefix}No changes required.")

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute the team-leads sync changes without mutating FreeIPA.",
        )

    @override
    def handle(self, *args, **options) -> None:
        dry_run: bool = bool(options.get("dry_run"))
        try:
            report = sync_materialized_team_leads_group(dry_run=dry_run)
        except Exception as exc:
            logger.exception(
                "freeipa_team_leads_sync failed source=%s destination=%s",
                settings.MATERIALIZED_TEAM_LEADS_SOURCE_GROUP_CN,
                settings.MATERIALIZED_TEAM_LEADS_DESTINATION_GROUP_CN,
            )
            raise CommandError(str(exc)) from exc

        self._write_report(report=report)

        if dry_run:
            return

        self.stdout.write(
            self.style.SUCCESS(
                "Synchronized "
                f"{settings.MATERIALIZED_TEAM_LEADS_DESTINATION_GROUP_CN} from "
                f"{settings.MATERIALIZED_TEAM_LEADS_SOURCE_GROUP_CN}."
            )
        )