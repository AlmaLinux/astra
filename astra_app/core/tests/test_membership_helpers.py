import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.membership import (
    build_pending_request_context,
    expiring_soon_cutoff,
    get_expiring_memberships,
    get_valid_memberships,
    remove_user_from_group,
    resolve_request_ids_by_membership_type,
)
from core.models import (
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)


class MembershipHelperTests(TestCase):
    def test_build_pending_request_context_returns_entries_and_category_index(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={"is_organization": True, "sort_order": 1},
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="mirror",
            defaults={"is_organization": True, "sort_order": 2},
        )

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "category_id": "mirror",
                "sort_order": 3,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Context Org", representative="bob")
        first = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
            status=MembershipRequest.Status.on_hold,
        )
        mirror = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="mirror",
            status=MembershipRequest.Status.pending,
        )

        ordered = list(
            MembershipRequest.objects.select_related("membership_type", "requested_organization")
            .filter(requested_organization=org)
            .order_by("requested_at", "pk")
        )

        context = build_pending_request_context(ordered, is_organization=True)

        self.assertEqual(len(context.entries), 3)
        self.assertEqual(context.category_ids, {"sponsorship", "mirror"})
        self.assertEqual(context.by_category["sponsorship"]["request_id"], first.pk)
        self.assertEqual(context.by_category["mirror"]["request_id"], mirror.pk)

    def test_membership_queryset_active_inclusive_by_default(self) -> None:
        MembershipType.objects.update_or_create(
            code="basic",
            defaults={
                "name": "Basic",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="timed",
            defaults={
                "name": "Timed",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="expired",
            defaults={
                "name": "Expired",
                "category_id": "individual",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="exact",
            defaults={
                "name": "Exact",
                "category_id": "individual",
                "sort_order": 3,
                "enabled": True,
            },
        )

        now = datetime.datetime(2026, 2, 2, 12, 0, 0, tzinfo=datetime.UTC)
        Membership.objects.create(
            target_username="alice",
            membership_type_id="basic",
            expires_at=None,
        )
        Membership.objects.create(
            target_username="bob",
            membership_type_id="timed",
            expires_at=now + datetime.timedelta(days=1),
        )
        Membership.objects.create(
            target_username="carol",
            membership_type_id="expired",
            expires_at=now - datetime.timedelta(days=1),
        )
        Membership.objects.create(
            target_username="dave",
            membership_type_id="exact",
            expires_at=now,
        )

        with patch("django.utils.timezone.now", autospec=True, return_value=now):
            memberships = list(Membership.objects.active())

        membership_ids = {m.membership_type_id for m in memberships}

        self.assertEqual(membership_ids, {"basic", "timed", "exact"})
    @override_settings(MEMBERSHIP_EXPIRING_SOON_DAYS=30)
    def test_expiring_soon_cutoff_uses_settings(self) -> None:
        frozen_now = datetime.datetime(2026, 2, 1, 12, 0, 0, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", autospec=True, return_value=frozen_now):
            cutoff = expiring_soon_cutoff()
        self.assertEqual(cutoff, frozen_now + datetime.timedelta(days=30))

    def test_get_expiring_memberships_filters_by_window(self) -> None:
        MembershipType.objects.update_or_create(
            code="basic",
            defaults={
                "name": "Basic",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

        now = datetime.datetime(2026, 2, 2, 12, 0, 0, tzinfo=datetime.UTC)
        Membership.objects.create(
            target_username="alice",
            membership_type_id="basic",
            expires_at=None,
        )
        Membership.objects.create(
            target_username="bob",
            membership_type_id="basic",
            expires_at=now,
        )
        Membership.objects.create(
            target_username="carol",
            membership_type_id="basic",
            expires_at=now + datetime.timedelta(days=1),
        )
        Membership.objects.create(
            target_username="dave",
            membership_type_id="basic",
            expires_at=now + datetime.timedelta(days=2),
        )
        Membership.objects.create(
            target_username="erin",
            membership_type_id="basic",
            expires_at=now + datetime.timedelta(days=3),
        )
        Membership.objects.create(
            target_username="frank",
            membership_type_id="basic",
            expires_at=now - datetime.timedelta(days=1),
        )

        with patch("django.utils.timezone.now", autospec=True, return_value=now):
            memberships = get_expiring_memberships(days=2)

        self.assertEqual(
            [membership.target_username for membership in memberships],
            ["carol", "dave"],
        )

    def test_get_valid_memberships_requires_single_target(self) -> None:
        org = Organization.objects.create(name="Acme", representative="bob")

        with self.assertRaises(ValueError):
            get_valid_memberships()

        with self.assertRaises(ValueError):
            get_valid_memberships(username="alice", organization=org)

    def test_get_valid_memberships_for_organization_filters_expired(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_organization": True,
                "sort_order": 1,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="mirror",
            defaults={
                "is_organization": True,
                "sort_order": 2,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="community",
            defaults={
                "is_organization": True,
                "sort_order": 3,
            },
        )

        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver",
                "category_id": "mirror",
                "sort_order": 2,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="expired",
            defaults={
                "name": "Expired",
                "category_id": "community",
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        now = datetime.datetime(2026, 2, 2, 12, 0, 0, tzinfo=datetime.UTC)

        Membership.objects.create(
            target_organization=org,
            membership_type_id="gold",
            expires_at=None,
        )
        Membership.objects.create(
            target_organization=org,
            membership_type_id="silver",
            expires_at=now + datetime.timedelta(days=10),
        )
        Membership.objects.create(
            target_organization=org,
            membership_type_id="expired",
            expires_at=now - datetime.timedelta(days=1),
        )

        with patch("django.utils.timezone.now", autospec=True, return_value=now):
            memberships = get_valid_memberships(organization=org)

        self.assertEqual([m.membership_type_id for m in memberships], ["gold", "silver"])

    def test_get_valid_memberships_for_username_includes_exact_expiry(self) -> None:
        MembershipType.objects.update_or_create(
            code="basic",
            defaults={
                "name": "Basic",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="exact",
            defaults={
                "name": "Exact",
                "category_id": "individual",
                "sort_order": 1,
                "enabled": True,
            },
        )

        now = datetime.datetime(2026, 2, 2, 12, 0, 0, tzinfo=datetime.UTC)
        Membership.objects.create(
            target_username="alice",
            membership_type_id="basic",
            expires_at=None,
        )
        Membership.objects.create(
            target_username="alice",
            membership_type_id="exact",
            expires_at=now,
        )

        with patch("django.utils.timezone.now", autospec=True, return_value=now):
            memberships = get_valid_memberships(username="alice")

        self.assertEqual([m.membership_type_id for m in memberships], ["basic", "exact"])

    def test_remove_user_from_group_handles_missing_user(self) -> None:
        with patch("core.membership.FreeIPAUser.get", return_value=None):
            self.assertFalse(remove_user_from_group(username="alice", group_cn="example"))

    def test_remove_user_from_group_returns_false_on_error(self) -> None:
        mock_user = SimpleNamespace(remove_from_group=lambda *args, **kwargs: None)

        def _raise(*_args, **_kwargs) -> None:
            raise RuntimeError("Boom")

        mock_user.remove_from_group = _raise

        with patch("core.membership.FreeIPAUser.get", return_value=mock_user):
            self.assertFalse(remove_user_from_group(username="alice", group_cn="example"))

    def test_remove_user_from_group_returns_true_on_success(self) -> None:
        def _ok(*_args, **_kwargs) -> None:
            return None

        mock_user = SimpleNamespace(remove_from_group=_ok)

        with patch("core.membership.FreeIPAUser.get", return_value=mock_user):
            self.assertTrue(remove_user_from_group(username="alice", group_cn="example"))

    def test_resolve_request_ids_by_membership_type_for_user(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

        req_individual = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.approved,
            decided_at=timezone.now(),
        )
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            membership_request=req_individual,
            action=MembershipLog.Action.approved,
        )

        req_mirror = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="mirror",
            status=MembershipRequest.Status.approved,
            decided_at=timezone.now(),
        )

        mapping = resolve_request_ids_by_membership_type(
            username="alice",
            membership_type_ids={"individual", "mirror"},
        )

        self.assertEqual(
            mapping,
            {
                "individual": req_individual.pk,
                "mirror": req_mirror.pk,
            },
        )

    def test_resolve_request_ids_by_membership_type_for_organization(self) -> None:
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="CERN", representative="carol")
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="gold",
            status=MembershipRequest.Status.approved,
            decided_at=timezone.now(),
        )
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="",
            target_organization=org,
            membership_type_id="gold",
            membership_request=req,
            action=MembershipLog.Action.approved,
        )

        mapping = resolve_request_ids_by_membership_type(
            organization=org,
            membership_type_ids={"gold"},
        )

        self.assertEqual(mapping, {"gold": req.pk})
