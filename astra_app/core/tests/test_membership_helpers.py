import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.membership import (
    FreeIPACallerMode,
    FreeIPAGroupRemovalOutcome,
    FreeIPAMissingUserPolicy,
    FreeIPARepresentativeSyncError,
    FreeIPARepresentativeSyncJournal,
    build_pending_request_context,
    compute_membership_requestability_context,
    expiring_soon_cutoff,
    get_expiring_memberships,
    get_valid_memberships,
    membership_target_filter,
    remove_organization_representative_from_group_if_present,
    remove_user_from_group,
    resolve_request_ids_by_membership_type,
    rollback_organization_representative_groups,
    sync_organization_representative_groups,
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
    def test_membership_target_filter_enforces_exactly_one_target(self) -> None:
        org = Organization.objects.create(name="Acme", representative="bob")

        with self.assertRaises(ValueError):
            membership_target_filter()

        with self.assertRaises(ValueError):
            membership_target_filter(username="alice", organization=org)

        self.assertIsNone(membership_target_filter(username="   "))
        self.assertEqual(
            membership_target_filter(username=" alice "),
            {"target_username": "alice"},
        )
        self.assertEqual(
            membership_target_filter(organization=org),
            {"target_organization": org},
        )

    def test_compute_membership_requestability_context_applies_held_category_exclusion(self) -> None:
        MembershipType.objects.update(enabled=False)

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "group_cn": "almalinux-silver",
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "category_id": "mirror",
                "sort_order": 2,
                "enabled": True,
                "group_cn": "almalinux-mirror",
            },
        )

        eligibility = {
            "valid_membership_type_codes": set(),
            "extendable_membership_type_codes": set(),
            "blocked_membership_type_codes": set(),
            "pending_membership_category_ids": set(),
        }
        org = Organization.objects.create(name="Req Org", representative="bob")

        context = compute_membership_requestability_context(
            organization=org,
            eligibility=SimpleNamespace(**eligibility),
            held_category_ids={"sponsorship", "mirror"},
        )
        self.assertFalse(context.membership_can_request_any)

        context_with_open_category = compute_membership_requestability_context(
            organization=org,
            eligibility=SimpleNamespace(**eligibility),
            held_category_ids={"sponsorship"},
        )
        self.assertTrue(context_with_open_category.membership_can_request_any)
        self.assertEqual(
            context_with_open_category.requestable_codes_by_category,
            {
                "sponsorship": {"silver"},
                "mirror": {"mirror"},
            },
        )

    def test_sync_representative_groups_raise_mode_exposes_partial_journal(self) -> None:
        old_user = FreeIPAUser("old", {"uid": ["old"], "memberof_group": ["g1", "g2"]})
        new_user = FreeIPAUser("new", {"uid": ["new"], "memberof_group": []})

        def _failing_add(user: FreeIPAUser, *, group_name: str) -> None:
            if group_name == "g2":
                raise RuntimeError("add failure")

        with (
            patch("core.membership.FreeIPAUser.get", side_effect=[old_user, new_user]),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock,
            patch.object(FreeIPAUser, "add_to_group", autospec=True, side_effect=_failing_add),
        ):
            with self.assertRaises(FreeIPARepresentativeSyncError) as ctx:
                sync_organization_representative_groups(
                    old_representative="old",
                    new_representative="new",
                    group_cns=("g1", "g2"),
                    caller_mode=FreeIPACallerMode.raise_on_error,
                    missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
                )

        self.assertEqual(remove_mock.call_count, 2)
        self.assertIn("g2", ctx.exception.result.failed_group_cns)
        self.assertIn("g1", ctx.exception.result.journal.new_added_group_cns)
        self.assertIn("g2", ctx.exception.result.journal.old_removed_group_cns)

    def test_rollback_representative_groups_scoped_to_journal_only(self) -> None:
        old_user = FreeIPAUser("old", {"uid": ["old"], "memberof_group": []})
        new_user = FreeIPAUser("new", {"uid": ["new"], "memberof_group": []})
        journal = FreeIPARepresentativeSyncJournal(
            targeted_group_cns=("g1", "g2", "g3"),
            skipped_group_cns=(),
            old_removed_group_cns=("g2",),
            new_added_group_cns=("g1",),
        )

        with (
            patch("core.membership.FreeIPAUser.get", side_effect=[old_user, new_user]),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_mock,
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock,
        ):
            rollback_organization_representative_groups(
                old_representative="old",
                new_representative="new",
                journal=journal,
            )

        remove_mock.assert_called_once()
        self.assertEqual(remove_mock.call_args.kwargs["group_name"], "g1")
        add_mock.assert_called_once()
        self.assertEqual(add_mock.call_args.kwargs["group_name"], "g2")

    def test_remove_organization_representative_from_group_if_present_blank_is_noop(self) -> None:
        outcome = remove_organization_representative_from_group_if_present(
            representative_username="",
            group_cn="",
            caller_mode=FreeIPACallerMode.best_effort,
            missing_user_policy=FreeIPAMissingUserPolicy.treat_as_noop,
        )
        self.assertEqual(outcome, FreeIPAGroupRemovalOutcome.noop_blank_input)

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
