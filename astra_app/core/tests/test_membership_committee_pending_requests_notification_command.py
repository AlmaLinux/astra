
import datetime
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from core.backends import FreeIPAGroup, FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType
from core.permissions import ASTRA_ADD_MEMBERSHIP


class MembershipCommitteePendingRequestsNotificationCommandTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _create_membership_type(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

    def test_command_sends_single_email_to_committee_when_pending_exists(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", return_value=frozen_now):
            self._create_membership_type()
            MembershipRequest.objects.create(requested_username="req1", membership_type_id="individual")

        committee_group = FreeIPAGroup(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, {"member_user": ["alice", "bob"]})

        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )
        bob = FreeIPAUser(
            "bob",
            {"uid": ["bob"], "mail": ["bob@example.com"], "memberof_group": []},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"alice": alice, "bob": bob}.get(username)

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
                    call_command("membership_pending_requests")

        from post_office.models import Email

        emails = Email.objects.filter(
            template__name=settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME
        )
        self.assertEqual(emails.count(), 1)
        msg = emails.first()
        assert msg is not None
        self.assertIn("alice@example.com", msg.to)
        self.assertIn("bob@example.com", msg.to)
        self.assertIn("/membership/requests/", str(msg.context))

    def test_command_does_not_send_when_no_pending(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", return_value=frozen_now):
            self._create_membership_type()

        committee_group = FreeIPAGroup(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, {"member_user": ["alice"]})
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", return_value=alice):
                    call_command("membership_pending_requests")

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                template__name=settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME
            ).exists()
        )

    def test_dry_run_does_not_queue_email(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", return_value=frozen_now):
            self._create_membership_type()
            MembershipRequest.objects.create(requested_username="req1", membership_type_id="individual")

        committee_group = FreeIPAGroup(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, {"member_user": ["alice"]})
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", return_value=alice):
                    call_command("membership_pending_requests", "--dry-run")

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                template__name=settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME
            ).exists()
        )

    def test_command_dedupes_same_day_without_force(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", return_value=frozen_now):
            self._create_membership_type()
            MembershipRequest.objects.create(requested_username="req1", membership_type_id="individual")

        committee_group = FreeIPAGroup(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, {"member_user": ["alice"]})
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        from post_office.models import Email

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", return_value=alice):
                    call_command("membership_pending_requests")
                    first = Email.objects.count()
                    call_command("membership_pending_requests")
                    second = Email.objects.count()

        self.assertEqual(first, second)

    def test_force_sends_even_if_already_sent_today(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", return_value=frozen_now):
            self._create_membership_type()
            MembershipRequest.objects.create(requested_username="req1", membership_type_id="individual")

        committee_group = FreeIPAGroup(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, {"member_user": ["alice"]})
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        from post_office.models import Email

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", return_value=alice):
                    call_command("membership_pending_requests")
                    first = Email.objects.count()
                    call_command("membership_pending_requests", "--force")
                    second = Email.objects.count()

        self.assertEqual(first + 1, second)

    def test_command_skips_if_already_sent_since_monday(self) -> None:
        frozen_monday = datetime.datetime(2026, 1, 5, 12, tzinfo=datetime.UTC)
        frozen_tuesday = datetime.datetime(2026, 1, 6, 12, tzinfo=datetime.UTC)

        with patch("django.utils.timezone.now", return_value=frozen_monday):
            self._create_membership_type()
            MembershipRequest.objects.create(requested_username="req1", membership_type_id="individual")

        committee_group = FreeIPAGroup(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, {"member_user": ["alice"]})
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        from post_office.models import Email

        with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
            with patch("core.backends.FreeIPAUser.get", return_value=alice):
                with patch("django.utils.timezone.now", return_value=frozen_monday):
                    call_command("membership_pending_requests")
                first = Email.objects.count()
                with patch("django.utils.timezone.now", return_value=frozen_tuesday):
                    call_command("membership_pending_requests")
                second = Email.objects.count()

        self.assertEqual(first, second)

    def test_command_sends_midweek_if_monday_had_no_pending(self) -> None:
        frozen_monday = datetime.datetime(2026, 1, 5, 12, tzinfo=datetime.UTC)
        frozen_tuesday = datetime.datetime(2026, 1, 6, 12, tzinfo=datetime.UTC)

        with patch("django.utils.timezone.now", return_value=frozen_monday):
            self._create_membership_type()

        committee_group = FreeIPAGroup(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, {"member_user": ["alice"]})
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        from post_office.models import Email

        with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
            with patch("core.backends.FreeIPAUser.get", return_value=alice):
                with patch("django.utils.timezone.now", return_value=frozen_monday):
                    call_command("membership_pending_requests")
                    self.assertEqual(Email.objects.count(), 0)

                with patch("django.utils.timezone.now", return_value=frozen_tuesday):
                    MembershipRequest.objects.create(requested_username="req1", membership_type_id="individual")
                    call_command("membership_pending_requests")

        self.assertEqual(
            Email.objects.filter(
                template__name=settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME
            ).count(),
            1,
        )
