from __future__ import annotations

import csv
from typing import Any, override

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.fas_user_attr_audit import audit_fas_user_attributes
from core.freeipa.user import FreeIPAUser


class Command(BaseCommand):
    help = "Audit FreeIPA users' fas* attributes for validation compliance."

    @override
    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--username",
            dest="username",
            default="",
            help="Audit only a single username (uid).",
        )
        parser.add_argument(
            "--include-non-canonical",
            action="store_true",
            help="Also report values that are accepted but would be normalized.",
        )
        parser.add_argument(
            "--csv",
            action="store_true",
            help="Output CSV instead of human-readable lines.",
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        username_filter: str = str(options.get("username") or "").strip()
        include_non_canonical: bool = bool(options.get("include_non_canonical"))
        as_csv: bool = bool(options.get("csv"))

        try:
            client = FreeIPAUser.get_client()
            result = client.user_find(o_all=True, o_no_members=False, o_sizelimit=0, o_timelimit=0)
        except Exception as exc:
            raise CommandError(f"Failed to query FreeIPA users: {exc}") from exc

        users = result.get("result", [])
        if not isinstance(users, list):
            raise CommandError("Unexpected FreeIPA response: result is not a list")

        excluded = {str(u).strip().lower() for u in settings.FREEIPA_FILTERED_USERNAMES}

        all_findings: list[tuple[str, str, str, str, str, str]] = []
        total_users = 0
        total_users_with_findings = 0

        for user_data in users:
            if not isinstance(user_data, dict):
                continue

            uid = user_data.get("uid")
            if isinstance(uid, list):
                username = uid[0] if uid else ""
            else:
                username = uid or ""
            username = str(username).strip()
            if not username:
                continue

            if username_filter and username != username_filter:
                continue

            if username.lower() in excluded:
                continue

            total_users += 1
            findings = audit_fas_user_attributes(
                username=username,
                user_data=user_data,
                include_non_canonical=include_non_canonical,
            )
            if not findings:
                continue

            total_users_with_findings += 1
            for f in findings:
                all_findings.append(
                    (
                        f.username,
                        f.attribute,
                        f.issue,
                        f.value,
                        f.suggested or "",
                        f.message,
                    )
                )

        if as_csv:
            writer = csv.writer(self.stdout)
            writer.writerow(["username", "attribute", "issue", "value", "suggested", "message"])
            writer.writerows(all_findings)
        else:
            for username, attribute, issue, value, suggested, message in all_findings:
                suggestion_text = f" -> {suggested}" if suggested else ""
                self.stdout.write(f"{username}: {attribute} [{issue}] {value!r}{suggestion_text} ({message})")

            self.stdout.write("")
            self.stdout.write(
                f"Audited {total_users} user(s); {total_users_with_findings} user(s) with findings; {len(all_findings)} finding(s)."
            )
