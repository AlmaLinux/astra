from __future__ import annotations

import csv
import io
import logging
from typing import Any, override

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.fas_user_attr_audit import audit_fas_user_attributes
from core.forms_selfservice import _get_timezones
from core.freeipa.user import FreeIPAUser
from core.ipa_user_attrs import (
    _add_change_list_setattr,
    _add_change_setattr,
    _data_get,
    _first,
    _split_lines,
    _update_user_attrs,
)
from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)


def _is_high_confidence_timezone_suggestion(*, suggested: str, valid_timezones: set[str]) -> bool:
    s = _normalize_str(suggested)
    if not s:
        return False
    # `_suggest_iana_timezone` uses a 'Candidates: ...' string when there is ambiguity.
    if s.startswith("Candidates:"):
        return False
    return s in valid_timezones


_MULTI_VALUED_FAS_ATTRS: frozenset[str] = frozenset(
    {
        "fasWebsiteUrl",
        "fasRssUrl",
        "fasIRCNick",
        "fasPronoun",
        "fasGPGKeyId",
    }
)


def _csv_line(values: list[str]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer).writerow(values)
    return buffer.getvalue().rstrip("\r\n")


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

        parser.add_argument(
            "--fix",
            action="store_true",
            help=(
                "Apply high-confidence fixes in FreeIPA for fasTimezone and values that are accepted but would be normalized. "
                "Only applies changes when the audit can produce a single, unambiguous canonical value."
            ),
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        username_filter: str = str(options.get("username") or "").strip()
        include_non_canonical: bool = bool(options.get("include_non_canonical"))
        as_csv: bool = bool(options.get("csv"))
        fix: bool = bool(options.get("fix"))

        # `--fix` implies we must compute non-canonical findings, even if the
        # caller didn't ask to report them.
        include_non_canonical_for_audit = include_non_canonical or fix

        valid_timezones: set[str] = set(_get_timezones())

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
        fixed_users = 0
        fixed_attributes = 0

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
                include_non_canonical=include_non_canonical_for_audit,
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

            if fix:
                desired_by_attr: dict[str, str] = {}
                for f in findings:
                    if not f.suggested:
                        continue

                    if f.issue == "non_canonical":
                        if f.attribute == "fasTimezone":
                            if _is_high_confidence_timezone_suggestion(
                                suggested=f.suggested,
                                valid_timezones=valid_timezones,
                            ):
                                desired_by_attr[f.attribute] = f.suggested
                        else:
                            desired_by_attr[f.attribute] = f.suggested
                        continue

                    # Only fix invalid timezone values when the audit can
                    # produce a single, valid IANA TZ name.
                    if f.attribute == "fasTimezone" and f.issue == "invalid":
                        if _is_high_confidence_timezone_suggestion(suggested=f.suggested, valid_timezones=valid_timezones):
                            desired_by_attr["fasTimezone"] = f.suggested

                if desired_by_attr:
                    addattrs: list[str] = []
                    setattrs: list[str] = []
                    delattrs: list[str] = []

                    for attr, desired in sorted(desired_by_attr.items()):
                        if attr in _MULTI_VALUED_FAS_ATTRS:
                            current_values = _data_get(user_data, attr, [])
                            _add_change_list_setattr(
                                addattrs=addattrs,
                                setattrs=setattrs,
                                delattrs=delattrs,
                                attr=attr,
                                current_values=current_values,
                                new_values=_split_lines(desired),
                            )
                        else:
                            current_value = _first(user_data, attr, "")
                            _add_change_setattr(
                                setattrs=setattrs,
                                delattrs=delattrs,
                                attr=attr,
                                current_value=current_value,
                                new_value=desired,
                            )

                    if addattrs or setattrs or delattrs:
                        skipped_attrs, applied = _update_user_attrs(
                            username,
                            addattrs=addattrs,
                            setattrs=setattrs,
                            delattrs=delattrs,
                        )

                        if applied:
                            fixed_users += 1
                            fixed_attributes += len(desired_by_attr)

                            logger.info(
                                "Fixed %s: add=%s set=%s del=%s",
                                username,
                                len(addattrs),
                                len(setattrs),
                                len(delattrs),
                            )

                        if skipped_attrs:
                            logger.warning(
                                "FreeIPA rejected attributes for %s: %s",
                                username,
                                ", ".join(sorted(skipped_attrs)),
                            )

        if as_csv:
            logger.info(_csv_line(["username", "attribute", "issue", "value", "suggested", "message"]))
            for row in all_findings:
                logger.info(_csv_line(list(row)))
        else:
            for username, attribute, issue, value, suggested, message in all_findings:
                suggestion_text = f" -> {suggested}" if suggested else ""
                logger.info("%s: %s [%s] %r%s (%s)", username, attribute, issue, value, suggestion_text, message)

            logger.info("")
            logger.info(
                "Audited %s user(s); %s user(s) with findings; %s finding(s).",
                total_users,
                total_users_with_findings,
                len(all_findings),
            )

        if fix:
            logger.info(
                "Applied fixes for %s user(s); %s attribute(s) updated.",
                fixed_users,
                fixed_attributes,
            )
