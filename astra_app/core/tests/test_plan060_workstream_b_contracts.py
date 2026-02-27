import datetime
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.membership_request_workflow import approve_membership_request
from core.models import (
    FreeIPAPermissionGrant,
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)
from core.permissions import (
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_USER_DIRECTORY,
)


class Plan060WorkstreamBContractTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_user_permission(self, username: str, permission: str) -> None:
        FreeIPAPermissionGrant.objects.update_or_create(
            permission=permission,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
        )

    def _create_membership_type(self, code: str, category_id: str, group_cn: str) -> MembershipType:
        MembershipTypeCategory.objects.update_or_create(
            pk=category_id,
            defaults={
                "is_individual": category_id != "sponsorship",
                "is_organization": category_id == "sponsorship",
                "sort_order": 0,
            },
        )

        membership_type, _ = MembershipType.objects.update_or_create(
            code=code,
            defaults={
                "name": code.title(),
                "group_cn": group_cn,
                "category_id": category_id,
                "sort_order": 0,
                "enabled": True,
            },
        )
        return membership_type

    def test_legacy_org_committee_routes_remain_arg_compatible_post_only_and_redirect_as_specified(self) -> None:
        gold = self._create_membership_type(code="gold", category_id="sponsorship", group_cn="sponsor-group")
        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type=gold,
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        self._grant_user_permission("reviewer", ASTRA_CHANGE_MEMBERSHIP)
        self._grant_user_permission("reviewer", ASTRA_DELETE_MEMBERSHIP)
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )
        representative = FreeIPAUser(
            "bob",
            {"uid": ["bob"], "mail": ["bob@example.com"], "memberof_group": ["sponsor-group"]},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return representative
            return None

        legacy_set_expiry_args = reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"])
        legacy_set_expiry_kwargs = reverse(
            "organization-sponsorship-set-expiry",
            kwargs={"organization_id": org.pk, "membership_type_code": "gold"},
        )
        self.assertEqual(legacy_set_expiry_args, legacy_set_expiry_kwargs)

        legacy_terminate_args = reverse("organization-sponsorship-terminate", args=[org.pk, "gold"])
        legacy_terminate_kwargs = reverse(
            "organization-sponsorship-terminate",
            kwargs={"organization_id": org.pk, "membership_type_code": "gold"},
        )
        self.assertEqual(legacy_terminate_args, legacy_terminate_kwargs)

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            get_expiry = self.client.get(legacy_set_expiry_args)
            get_terminate = self.client.get(legacy_terminate_args)
        self.assertEqual(get_expiry.status_code, 404)
        self.assertEqual(get_terminate.status_code, 404)

        referer_target = "/organizations/?from=contract"
        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user):
            set_expiry_response = self.client.post(
                legacy_set_expiry_args,
                data={"expires_on": (timezone.now() + datetime.timedelta(days=45)).date().isoformat()},
                HTTP_REFERER=referer_target,
                follow=False,
            )
        self.assertEqual(set_expiry_response.status_code, 302)
        self.assertEqual(set_expiry_response["Location"], referer_target)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_from_group,
        ):
            terminate_response = self.client.post(legacy_terminate_args, follow=False)
        self.assertEqual(terminate_response.status_code, 302)
        self.assertEqual(
            terminate_response["Location"],
            reverse("organization-detail", kwargs={"organization_id": org.pk}),
        )
        remove_from_group.assert_called_once_with(representative, group_name="sponsor-group")

    def test_user_expiry_before_today_utc_is_rejected_and_membership_remains_active(self) -> None:
        membership_type = self._create_membership_type(
            code="individual",
            category_id="individual",
            group_cn="almalinux-individual",
        )
        Membership.objects.create(
            target_username="alice",
            membership_type=membership_type,
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        self._grant_user_permission("reviewer", ASTRA_CHANGE_MEMBERSHIP)
        self._grant_user_permission("reviewer", ASTRA_DELETE_MEMBERSHIP)
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": ["almalinux-individual"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        original_expiry = Membership.objects.get(
            target_username="alice", membership_type_id="individual"
        ).expires_at
        self.assertIsNotNone(original_expiry)

        yesterday_utc = timezone.now().astimezone(datetime.UTC).date() - datetime.timedelta(days=1)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_from_group,
        ):
            response = self.client.post(
                reverse("membership-set-expiry", args=["alice", "individual"]),
                data={"expires_on": yesterday_utc.isoformat()},
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user-profile", kwargs={"username": "alice"}))
        membership = Membership.objects.filter(target_username="alice", membership_type_id="individual").first()
        self.assertIsNotNone(membership)
        self.assertIsNotNone(membership.expires_at)
        self.assertEqual(membership.expires_at, original_expiry)
        self.assertTrue(Membership.objects.active().filter(target_username="alice", membership_type_id="individual").exists())

        actions = list(
            MembershipLog.objects.filter(target_username="alice", membership_type_id="individual")
            .order_by("id")
            .values_list("action", flat=True)
        )
        self.assertEqual(
            actions,
            [],
        )
        remove_from_group.assert_not_called()

    def test_org_expiry_before_today_utc_is_rejected_and_membership_remains_active(self) -> None:
        gold = self._create_membership_type(code="gold", category_id="sponsorship", group_cn="sponsor-group")
        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type=gold,
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        self._grant_user_permission("reviewer", ASTRA_CHANGE_MEMBERSHIP)
        self._grant_user_permission("reviewer", ASTRA_DELETE_MEMBERSHIP)
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )
        bob = FreeIPAUser(
            "bob",
            {"uid": ["bob"], "mail": ["bob@example.com"], "memberof_group": ["sponsor-group"]},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        original_expiry = Membership.objects.get(
            target_organization=org, membership_type_id="gold"
        ).expires_at
        self.assertIsNotNone(original_expiry)

        yesterday_utc = timezone.now().astimezone(datetime.UTC).date() - datetime.timedelta(days=1)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_from_group,
        ):
            response = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={"expires_on": yesterday_utc.isoformat()},
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        membership = Membership.objects.filter(target_organization=org, membership_type_id="gold").first()
        self.assertIsNotNone(membership)
        self.assertIsNotNone(membership.expires_at)
        self.assertEqual(membership.expires_at, original_expiry)
        self.assertTrue(Membership.objects.active().filter(target_organization=org, membership_type_id="gold").exists())

        actions = list(
            MembershipLog.objects.filter(target_organization=org, membership_type_id="gold")
            .order_by("id")
            .values_list("action", flat=True)
        )
        self.assertEqual(
            actions,
            [],
        )
        remove_from_group.assert_not_called()

    def test_user_expiry_today_utc_is_accepted_and_does_not_force_termination(self) -> None:
        membership_type = self._create_membership_type(
            code="individual",
            category_id="individual",
            group_cn="almalinux-individual",
        )
        Membership.objects.create(
            target_username="alice",
            membership_type=membership_type,
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        self._grant_user_permission("reviewer", ASTRA_CHANGE_MEMBERSHIP)
        self._grant_user_permission("reviewer", ASTRA_DELETE_MEMBERSHIP)
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": ["almalinux-individual"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        today_utc = timezone.now().astimezone(datetime.UTC).date()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_from_group,
        ):
            response = self.client.post(
                reverse("membership-set-expiry", args=["alice", "individual"]),
                data={"expires_on": today_utc.isoformat()},
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user-profile", kwargs={"username": "alice"}))

        membership = Membership.objects.filter(target_username="alice", membership_type_id="individual").first()
        self.assertIsNotNone(membership)
        if membership is None:
            self.fail("Expected membership row for alice/individual")
        self.assertEqual(
            membership.expires_at,
            datetime.datetime.combine(today_utc, datetime.time(23, 59, 59), tzinfo=datetime.UTC),
        )

        actions = list(
            MembershipLog.objects.filter(target_username="alice", membership_type_id="individual")
            .order_by("id")
            .values_list("action", flat=True)
        )
        self.assertEqual(actions, [MembershipLog.Action.expiry_changed])
        remove_from_group.assert_not_called()

    def test_org_expiry_today_utc_is_accepted_and_does_not_force_termination(self) -> None:
        gold = self._create_membership_type(code="gold", category_id="sponsorship", group_cn="sponsor-group")
        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type=gold,
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        self._grant_user_permission("reviewer", ASTRA_CHANGE_MEMBERSHIP)
        self._grant_user_permission("reviewer", ASTRA_DELETE_MEMBERSHIP)
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )
        bob = FreeIPAUser(
            "bob",
            {"uid": ["bob"], "mail": ["bob@example.com"], "memberof_group": ["sponsor-group"]},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        today_utc = timezone.now().astimezone(datetime.UTC).date()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_from_group,
        ):
            response = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={"expires_on": today_utc.isoformat()},
                follow=False,
            )

        self.assertEqual(response.status_code, 302)

        membership = Membership.objects.filter(target_organization=org, membership_type_id="gold").first()
        self.assertIsNotNone(membership)
        if membership is None:
            self.fail("Expected membership row for org/gold")
        self.assertEqual(
            membership.expires_at,
            datetime.datetime.combine(today_utc, datetime.time(23, 59, 59), tzinfo=datetime.UTC),
        )

        actions = list(
            MembershipLog.objects.filter(target_organization=org, membership_type_id="gold")
            .order_by("id")
            .values_list("action", flat=True)
        )
        self.assertEqual(actions, [MembershipLog.Action.expiry_changed])
        remove_from_group.assert_not_called()

    def test_user_rejected_past_expiry_then_terminate_is_idempotent_for_logs_and_freeipa_calls(self) -> None:
        membership_type = self._create_membership_type(
            code="individual",
            category_id="individual",
            group_cn="almalinux-individual",
        )
        Membership.objects.create(
            target_username="alice",
            membership_type=membership_type,
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        self._grant_user_permission("reviewer", ASTRA_CHANGE_MEMBERSHIP)
        self._grant_user_permission("reviewer", ASTRA_DELETE_MEMBERSHIP)
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": ["almalinux-individual"],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        yesterday_utc = timezone.now().astimezone(datetime.UTC).date() - datetime.timedelta(days=1)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_from_group,
        ):
            first_expiry = self.client.post(
                reverse("membership-set-expiry", args=["alice", "individual"]),
                data={"expires_on": yesterday_utc.isoformat()},
                follow=False,
            )
            second_expiry = self.client.post(
                reverse("membership-set-expiry", args=["alice", "individual"]),
                data={"expires_on": yesterday_utc.isoformat()},
                follow=False,
            )
            retry_terminate = self.client.post(
                reverse("membership-terminate", args=["alice", "individual"]),
                follow=False,
            )

        self.assertEqual(first_expiry.status_code, 302)
        self.assertEqual(second_expiry.status_code, 302)
        self.assertEqual(retry_terminate.status_code, 302)

        actions = list(
            MembershipLog.objects.filter(target_username="alice", membership_type_id="individual")
            .order_by("id")
            .values_list("action", flat=True)
        )
        self.assertEqual(
            actions,
            [MembershipLog.Action.terminated],
        )
        remove_from_group.assert_called_once_with(alice, group_name="almalinux-individual")

    def test_org_rejected_past_expiry_then_terminate_is_idempotent_for_logs_and_freeipa_calls(self) -> None:
        gold = self._create_membership_type(code="gold", category_id="sponsorship", group_cn="sponsor-group")
        org = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=org,
            membership_type=gold,
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )

        self._grant_user_permission("reviewer", ASTRA_CHANGE_MEMBERSHIP)
        self._grant_user_permission("reviewer", ASTRA_DELETE_MEMBERSHIP)
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )
        bob = FreeIPAUser(
            "bob",
            {"uid": ["bob"], "mail": ["bob@example.com"], "memberof_group": ["sponsor-group"]},
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        yesterday_utc = timezone.now().astimezone(datetime.UTC).date() - datetime.timedelta(days=1)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch.object(FreeIPAUser, "remove_from_group", autospec=True) as remove_from_group,
        ):
            first_expiry = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={"expires_on": yesterday_utc.isoformat()},
                follow=False,
            )
            second_expiry = self.client.post(
                reverse("organization-sponsorship-set-expiry", args=[org.pk, "gold"]),
                data={"expires_on": yesterday_utc.isoformat()},
                follow=False,
            )
            retry_terminate = self.client.post(
                reverse("organization-sponsorship-terminate", args=[org.pk, "gold"]),
                follow=False,
            )

        self.assertEqual(first_expiry.status_code, 302)
        self.assertEqual(second_expiry.status_code, 302)
        self.assertEqual(retry_terminate.status_code, 302)

        actions = list(
            MembershipLog.objects.filter(target_organization=org, membership_type_id="gold")
            .order_by("id")
            .values_list("action", flat=True)
        )
        self.assertEqual(
            actions,
            [MembershipLog.Action.terminated],
        )
        remove_from_group.assert_called_once_with(bob, group_name="sponsor-group")

    def test_committee_approval_requires_agreement_gate_to_be_satisfied(self) -> None:
        membership_type = self._create_membership_type(
            code="individual",
            category_id="individual",
            group_cn="almalinux-individual",
        )
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type=membership_type,
        )

        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                side_effect=[["cla"], []],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=alice),
            patch.object(FreeIPAUser, "add_to_group", autospec=True),
        ):
            with self.assertRaisesRegex(ValidationError, "required agreements"):
                approve_membership_request(
                    membership_request=membership_request,
                    actor_username="reviewer",
                    send_approved_email=False,
                )

            approve_membership_request(
                membership_request=membership_request,
                actor_username="reviewer",
                send_approved_email=False,
            )

        membership_request.refresh_from_db()
        self.assertEqual(membership_request.status, MembershipRequest.Status.approved)

    def test_users_directory_requires_explicit_view_user_directory_permission_grant(self) -> None:
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "mail": ["reviewer@example.com"], "memberof_group": []},
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[reviewer]),
        ):
            denied = self.client.get(reverse("users"))
        self.assertEqual(denied.status_code, 404)

        self._grant_user_permission("reviewer", ASTRA_VIEW_USER_DIRECTORY)

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[reviewer]),
        ):
            allowed = self.client.get(reverse("users"))
        self.assertEqual(allowed.status_code, 200)
        self.assertContains(allowed, "reviewer")
