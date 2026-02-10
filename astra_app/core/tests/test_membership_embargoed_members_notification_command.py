
import datetime
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.backends import FreeIPAGroup, FreeIPAUser
from core.models import FreeIPAPermissionGrant, Membership, MembershipType
from core.permissions import ASTRA_ADD_MEMBERSHIP


class MembershipEmbargoedMembersNotificationCommandTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def _create_membership_type(self) -> MembershipType:
        membership_type, _created = MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        return membership_type

    def _country_attr_data(self, code: str) -> dict[str, list[str]]:
        country_attr = settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR
        return {country_attr: [code]}

    @override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["RU"])
    def test_command_sends_email_with_embargoed_members(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        membership_type = self._create_membership_type()

        Membership.objects.create(
            target_username="member1",
            membership_type=membership_type,
            expires_at=frozen_now + datetime.timedelta(days=10),
        )

        committee_group = FreeIPAGroup(
            settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            {"member_user": ["alice", "bob"]},
        )

        member1 = FreeIPAUser(
            "member1",
            {
                "uid": ["member1"],
                "mail": ["member1@example.com"],
                "displayname": ["Member One"],
                **self._country_attr_data("RU"),
            },
        )
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )
        bob = FreeIPAUser(
            "bob",
            {"uid": ["bob"], "mail": ["bob@example.com"], "memberof_group": []},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"member1": member1, "alice": alice, "bob": bob}.get(username)

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
                    call_command("membership_embargoed_members")

        from post_office.models import Email

        email = Email.objects.filter(
            template__name=settings.MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME
        ).first()
        self.assertIsNotNone(email)
        assert email is not None
        self.assertIn("alice@example.com", email.to)
        self.assertIn("bob@example.com", email.to)
        ctx = dict(email.context or {})
        self.assertEqual(ctx.get("embargoed_count"), 1)
        self.assertEqual(len(ctx.get("embargoed_members") or []), 1)
        self.assertIn("RU", str(ctx.get("embargoed_members")))

    @override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["RU", "IR"])
    def test_command_sends_email_with_two_embargoed_members(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        membership_type = self._create_membership_type()

        Membership.objects.create(
            target_username="member1",
            membership_type=membership_type,
            expires_at=frozen_now + datetime.timedelta(days=10),
        )
        Membership.objects.create(
            target_username="member2",
            membership_type=membership_type,
            expires_at=frozen_now + datetime.timedelta(days=10),
        )

        committee_group = FreeIPAGroup(
            settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            {"member_user": ["alice"]},
        )

        member1 = FreeIPAUser(
            "member1",
            {
                "uid": ["member1"],
                "mail": ["member1@example.com"],
                "displayname": ["Member One"],
                **self._country_attr_data("RU"),
            },
        )
        member2 = FreeIPAUser(
            "member2",
            {
                "uid": ["member2"],
                "mail": ["member2@example.com"],
                "displayname": ["Member Two"],
                **self._country_attr_data("IR"),
            },
        )
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"member1": member1, "member2": member2, "alice": alice}.get(username)

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
                    call_command("membership_embargoed_members")

        from post_office.models import Email

        email = Email.objects.filter(
            template__name=settings.MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME
        ).latest("created")
        ctx = dict(email.context or {})
        self.assertEqual(ctx.get("embargoed_count"), 2)
        members = list(ctx.get("embargoed_members") or [])
        self.assertEqual(len(members), 2)
        codes = {m.get("country_code") for m in members}
        self.assertEqual(codes, {"RU", "IR"})

    @override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["RU"])
    def test_command_skips_when_no_embargoed_members(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        membership_type = self._create_membership_type()

        Membership.objects.create(
            target_username="member1",
            membership_type=membership_type,
            expires_at=frozen_now + datetime.timedelta(days=10),
        )

        committee_group = FreeIPAGroup(
            settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            {"member_user": ["alice"]},
        )

        member1 = FreeIPAUser(
            "member1",
            {
                "uid": ["member1"],
                "mail": ["member1@example.com"],
                "displayname": ["Member One"],
                **self._country_attr_data("US"),
            },
        )
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"member1": member1, "alice": alice}.get(username)

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
                    call_command("membership_embargoed_members")

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                template__name=settings.MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME
            ).exists()
        )

    @override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["RU"])
    def test_dry_run_does_not_queue_email(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        membership_type = self._create_membership_type()

        Membership.objects.create(
            target_username="member1",
            membership_type=membership_type,
            expires_at=frozen_now + datetime.timedelta(days=10),
        )

        committee_group = FreeIPAGroup(
            settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            {"member_user": ["alice"]},
        )

        member1 = FreeIPAUser(
            "member1",
            {
                "uid": ["member1"],
                "mail": ["member1@example.com"],
                "displayname": ["Member One"],
                **self._country_attr_data("RU"),
            },
        )
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"member1": member1, "alice": alice}.get(username)

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
                    call_command("membership_embargoed_members", "--dry-run")

        from post_office.models import Email

        self.assertFalse(
            Email.objects.filter(
                template__name=settings.MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME
            ).exists()
        )

    @override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["RU"])
    def test_command_dedupes_same_day_without_force(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        membership_type = self._create_membership_type()

        Membership.objects.create(
            target_username="member1",
            membership_type=membership_type,
            expires_at=frozen_now + datetime.timedelta(days=10),
        )

        committee_group = FreeIPAGroup(
            settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            {"member_user": ["alice"]},
        )

        member1 = FreeIPAUser(
            "member1",
            {
                "uid": ["member1"],
                "mail": ["member1@example.com"],
                "displayname": ["Member One"],
                **self._country_attr_data("RU"),
            },
        )
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"member1": member1, "alice": alice}.get(username)

        from post_office.models import Email

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
                    call_command("membership_embargoed_members")
                    first_count = Email.objects.count()
                    call_command("membership_embargoed_members")
                    second_count = Email.objects.count()

        self.assertEqual(first_count, second_count)

    @override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["RU"])
    def test_force_sends_even_if_already_sent_today(self) -> None:
        frozen_now = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.UTC)
        membership_type = self._create_membership_type()

        Membership.objects.create(
            target_username="member1",
            membership_type=membership_type,
            expires_at=frozen_now + datetime.timedelta(days=10),
        )

        committee_group = FreeIPAGroup(
            settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            {"member_user": ["alice"]},
        )

        member1 = FreeIPAUser(
            "member1",
            {
                "uid": ["member1"],
                "mail": ["member1@example.com"],
                "displayname": ["Member One"],
                **self._country_attr_data("RU"),
            },
        )
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            return {"member1": member1, "alice": alice}.get(username)

        from post_office.models import Email

        with patch("django.utils.timezone.now", return_value=frozen_now):
            with patch("core.backends.FreeIPAGroup.get", return_value=committee_group):
                with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
                    call_command("membership_embargoed_members")
                    first_count = Email.objects.count()
                    call_command("membership_embargoed_members", "--force")
                    second_count = Email.objects.count()

        self.assertEqual(first_count + 1, second_count)
