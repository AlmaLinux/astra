from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.membership import FreeIPACallerMode, FreeIPAMissingUserPolicy
from core.membership_request_workflow import (
    approve_membership_request,
    ignore_membership_request,
    put_membership_request_on_hold,
    reject_membership_request,
    reopen_ignored_membership_request,
    resubmit_membership_request,
)
from core.models import Membership, MembershipRequest, MembershipType, MembershipTypeCategory, Organization


class MembershipRequestWorkflowRaceConditionTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 1,
            },
        )
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
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="platinum",
            defaults={
                "name": "Platinum",
                "group_cn": "almalinux-platinum",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

    def test_approve_rechecks_status_from_db_for_stale_request(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        stale_request = membership_request
        MembershipRequest.objects.filter(pk=membership_request.pk).update(status=MembershipRequest.Status.approved)

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=[],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=alice),
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock,
        ):
            with self.assertRaisesMessage(ValidationError, "Only pending requests can be approved"):
                approve_membership_request(
                    membership_request=stale_request,
                    actor_username="reviewer",
                    send_approved_email=False,
                )

        add_mock.assert_not_called()

    def test_reject_rechecks_status_from_db_for_stale_request(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        stale_request = membership_request
        MembershipRequest.objects.filter(pk=membership_request.pk).update(status=MembershipRequest.Status.approved)

        with self.assertRaisesMessage(ValidationError, "Only pending or on-hold requests can be rejected"):
            reject_membership_request(
                membership_request=stale_request,
                actor_username="reviewer",
                rejection_reason="reason",
                send_rejected_email=False,
            )

    def test_ignore_rechecks_status_from_db_for_stale_request(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        stale_request = membership_request
        MembershipRequest.objects.filter(pk=membership_request.pk).update(status=MembershipRequest.Status.approved)

        with self.assertRaisesMessage(ValidationError, "Only pending or on-hold requests can be ignored"):
            ignore_membership_request(
                membership_request=stale_request,
                actor_username="reviewer",
            )

    def test_put_on_hold_rechecks_status_from_db_for_stale_request(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        stale_request = membership_request
        MembershipRequest.objects.filter(pk=membership_request.pk).update(status=MembershipRequest.Status.approved)

        with self.assertRaisesMessage(ValidationError, "Only pending requests can be put on hold"):
            put_membership_request_on_hold(
                membership_request=stale_request,
                actor_username="reviewer",
                rfi_message="Need more details",
                send_rfi_email=False,
                application_url="https://example.test/membership/request",
            )

    def test_reopen_rechecks_status_from_db_for_stale_request(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.ignored,
        )
        stale_request = membership_request
        MembershipRequest.objects.filter(pk=membership_request.pk).update(status=MembershipRequest.Status.pending)

        with self.assertRaisesMessage(ValidationError, "Only ignored requests can be reopened"):
            reopen_ignored_membership_request(
                membership_request=stale_request,
                actor_username="reviewer",
            )

    def test_reopen_blocks_when_another_open_request_exists_for_same_target_and_type(self) -> None:
        ignored_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.ignored,
        )
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Cannot reopen: an open request already exists for this target and membership type.",
        ):
            reopen_ignored_membership_request(
                membership_request=ignored_request,
                actor_username="reviewer",
            )

    def test_resubmit_requires_on_hold_status(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
            responses=[{"Question": "Original"}],
        )

        with self.assertRaisesMessage(ValidationError, "Only on-hold requests can be resubmitted"):
            resubmit_membership_request(
                membership_request=membership_request,
                actor_username="alice",
                updated_responses=[{"Question": "Updated"}],
            )

    def test_resubmit_maps_integrity_error_to_validation_error(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            responses=[{"Question": "Original"}],
        )

        with patch.object(
            MembershipRequest,
            "save",
            autospec=True,
            side_effect=IntegrityError("duplicate key value violates unique constraint"),
        ):
            with self.assertRaisesMessage(
                ValidationError,
                "Cannot resubmit: a conflicting open request exists for this target and membership type.",
            ):
                resubmit_membership_request(
                    membership_request=membership_request,
                    actor_username="alice",
                    updated_responses=[{"Question": "Updated"}],
                )

    def test_approve_user_schedules_group_add_on_commit(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=[],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=alice),
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock,
        ):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                approve_membership_request(
                    membership_request=membership_request,
                    actor_username="reviewer",
                    send_approved_email=False,
                )

            self.assertGreaterEqual(len(callbacks), 1)
            add_mock.assert_not_called()
            for callback in callbacks:
                callback()

            add_mock.assert_called_once_with(alice, group_name="almalinux-individual")

    def test_approve_user_does_not_mutate_group_when_db_write_fails(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=[],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=alice),
            patch.object(MembershipRequest, "save", autospec=True, side_effect=RuntimeError("db write failed")),
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock,
        ):
            with self.assertRaisesRegex(RuntimeError, "db write failed"):
                approve_membership_request(
                    membership_request=membership_request,
                    actor_username="reviewer",
                    send_approved_email=False,
                )

        add_mock.assert_not_called()

    def test_approve_org_schedules_group_sync_on_commit(self) -> None:
        organization = Organization.objects.create(name="Acme", representative="bob")
        Membership.objects.create(
            target_organization=organization,
            membership_type_id="gold",
            expires_at=timezone.now(),
        )
        membership_request = MembershipRequest.objects.create(
            requested_organization=organization,
            requested_username="",
            membership_type_id="platinum",
            status=MembershipRequest.Status.pending,
        )
        representative = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": ["almalinux-gold"],
            },
        )

        with (
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=[],
            ),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=representative),
            patch(
                "core.membership_request_workflow.sync_organization_representative_membership_groups"
            ) as sync_helper,
        ):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                approve_membership_request(
                    membership_request=membership_request,
                    actor_username="reviewer",
                    send_approved_email=False,
                )

            self.assertGreaterEqual(len(callbacks), 1)
            sync_helper.assert_not_called()
            for callback in callbacks:
                callback()

            sync_helper.assert_called_once_with(
                representative_username="bob",
                group_cns=("almalinux-platinum",),
                old_group_cn_to_remove="almalinux-gold",
                membership_request_id=membership_request.pk,
                log_prefix="approve_membership_request",
                caller_mode=FreeIPACallerMode.raise_on_error,
                missing_user_policy=FreeIPAMissingUserPolicy.treat_as_error,
            )
