import io
import json

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.country_codes import country_attr_name, country_label_from_code
from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import get_e2e_service_client
from core.membership_notes import CUSTOS
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Note
from core.tests.utils_test_data import ensure_core_categories


class MembershipCommitteeResetCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        ensure_core_categories()

    @override_settings(ASTRA_E2E_MODE=False, ASTRA_E2E_FAKE_FREEIPA_ENABLED=False)
    def test_command_rejects_runs_outside_fake_freeipa_e2e_mode(self) -> None:
        with self.assertRaisesMessage(CommandError, "ASTRA_E2E_FAKE_FREEIPA_ENABLED"):
            call_command("membership_committee_reset")

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_wave2_committee_scenarios_idempotently(self) -> None:
        stdout_first = io.StringIO()
        stdout_second = io.StringIO()

        call_command("membership_committee_reset", stdout=stdout_first)
        first_payload = json.loads(stdout_first.getvalue())

        call_command("membership_committee_reset", stdout=stdout_second)
        second_payload = json.loads(stdout_second.getvalue())

        self.assertEqual(first_payload["scenario"], "membership-committee")
        self.assertEqual(second_payload["scenario"], "membership-committee")
        self.assertEqual(first_payload["actors"].keys(), second_payload["actors"].keys())
        self.assertEqual(set(first_payload["actors"].keys()), {"committee_reviewer"})

        reviewer_payload = first_payload["actors"]["committee_reviewer"]
        scenario_aliases = first_payload["scenarios"]

        self.assertEqual(reviewer_payload["username"], "regular01")
        self.assertEqual(
            set(reviewer_payload["request_aliases"].keys()),
            {
                "pending_shell_observer",
                "on_hold_shell_observer",
                "pending_filter_renewal",
                "pending_filter_nonrenewal",
                "pending_row_action",
                "pending_row_approve",
                "pending_row_reject",
                "pending_row_rfi",
                "pending_bulk_accept_primary",
                "pending_bulk_accept_secondary",
                "pending_bulk_select_all_extra",
                "on_hold_bulk_approve_primary",
                "on_hold_bulk_approve_secondary",
                "on_hold_bulk_select_all_extra",
                "on_hold_row_approve",
            },
        )
        self.assertEqual(
            set(scenario_aliases.keys()),
            {
                "committee-queue-shell",
                "committee-pending-filter-renewals",
                "committee-pending-row-actions",
                "committee-pending-bulk-accept",
                "committee-on-hold-bulk-approve",
                "committee-row-actions",
                "committee-request-detail",
            },
        )
        self.assertEqual(
            scenario_aliases["committee-pending-bulk-accept"]["aliases"],
            [
                "pending_bulk_accept_primary",
                "pending_bulk_accept_secondary",
                "pending_bulk_select_all_extra",
            ],
        )
        self.assertTrue(scenario_aliases["committee-pending-bulk-accept"]["destructive"])
        self.assertFalse(scenario_aliases["committee-queue-shell"]["destructive"])

        self.assertTrue(MembershipType.objects.get(code="individual").enabled)
        self.assertTrue(MembershipType.objects.get(code="mirror").enabled)

        seeded_requests = list(
            MembershipRequest.objects.order_by("pk").values_list(
                "requested_username",
                "membership_type_id",
                "status",
            )
        )
        self.assertGreaterEqual(len(seeded_requests), 11)
        self.assertGreaterEqual(
            sum(status == MembershipRequest.Status.pending for _username, _type_code, status in seeded_requests),
            7,
        )
        self.assertGreaterEqual(
            sum(status == MembershipRequest.Status.on_hold for _username, _type_code, status in seeded_requests),
            4,
        )
        self.assertTrue(
            MembershipRequest.objects.filter(
                requested_username="regular04",
                membership_type_id="mirror",
                status=MembershipRequest.Status.pending,
            ).exists()
        )
        self.assertTrue(
            MembershipRequest.objects.filter(
                requested_username="regular09",
                membership_type_id="mirror",
                status=MembershipRequest.Status.on_hold,
            ).exists()
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_clears_previous_committee_slice_before_reseeding(self) -> None:
        call_command("membership_committee_reset")

        seeded_request = MembershipRequest.objects.get(
            requested_username="regular05",
            membership_type_id="mirror",
            status=MembershipRequest.Status.pending,
        )
        Note.objects.create(
            membership_request=seeded_request,
            username="regular01",
            content="Temporary note that should be removed by reset.",
        )
        temporary_log = MembershipLog.objects.create(
            actor_username="regular01",
            target_username="regular05",
            membership_type=MembershipType.objects.get(code="individual"),
            membership_request=seeded_request,
            action=MembershipLog.Action.requested,
        )

        call_command("membership_committee_reset")

        self.assertGreaterEqual(Note.objects.count(), 1)
        self.assertFalse(MembershipLog.objects.filter(pk=temporary_log.pk).exists())
        self.assertGreater(MembershipLog.objects.count(), 0)
        self.assertGreaterEqual(MembershipRequest.objects.count(), 11)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_preserves_non_committee_memberships_for_shared_e2e_actors(self) -> None:
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )
        Membership.objects.create(target_username="regular03", membership_type_id="mirror")

        call_command("membership_committee_reset")

        self.assertTrue(Membership.objects.filter(target_username="regular03", membership_type_id="mirror").exists())
        self.assertTrue(Membership.objects.filter(target_username="regular04", membership_type_id="mirror").exists())

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_assigns_committee_actor_and_signs_coc(self) -> None:
        call_command("membership_committee_reset")

        client = get_e2e_service_client()
        reviewer = client.user_show("regular01")["result"]

        self.assertIn(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, reviewer.get("memberof_group", []))
        self.assertEqual(reviewer.get("c"), ["US"])

        agreement = FreeIPAFASAgreement.get("AlmaLinux Community Code of Conduct")
        self.assertIsNotNone(agreement)
        self.assertIn("regular01", agreement.users)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_keeps_wave2_on_hold_observer_and_bulk_aliases_on_first_page(self) -> None:
        stdout = io.StringIO()

        call_command("membership_committee_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        request_aliases = payload["actors"]["committee_reviewer"]["request_aliases"]
        first_page_request_ids = set(
            MembershipRequest.objects.filter(status=MembershipRequest.Status.on_hold)
            .order_by("on_hold_at", "pk")
            .values_list("pk", flat=True)[:10]
        )

        self.assertTrue(
            {
                request_aliases["on_hold_shell_observer"],
                request_aliases["on_hold_bulk_approve_primary"],
                request_aliases["on_hold_bulk_approve_secondary"],
                request_aliases["on_hold_bulk_select_all_extra"],
            }.issubset(first_page_request_ids)
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_keeps_on_hold_timestamps_aligned_with_workflow_history(self) -> None:
        stdout = io.StringIO()

        call_command("membership_committee_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        request_id = payload["actors"]["committee_reviewer"]["request_aliases"]["on_hold_shell_observer"]
        membership_request = MembershipRequest.objects.get(pk=request_id)
        on_hold_log = MembershipLog.objects.get(
            membership_request=membership_request,
            action=MembershipLog.Action.on_hold,
        )

        self.assertIsNotNone(membership_request.on_hold_at)
        assert membership_request.on_hold_at is not None
        self.assertGreater(membership_request.on_hold_at, membership_request.requested_at)
        self.assertEqual(membership_request.on_hold_at, on_hold_log.created_at)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_keeps_queue_shell_pending_observer_on_first_pending_page(self) -> None:
        stdout = io.StringIO()

        call_command("membership_committee_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        request_aliases = payload["actors"]["committee_reviewer"]["request_aliases"]
        first_page_request_ids = set(
            MembershipRequest.objects.filter(status=MembershipRequest.Status.pending)
            .order_by("requested_at", "pk")
            .values_list("pk", flat=True)[:10]
        )

        self.assertIn(request_aliases["pending_shell_observer"], first_page_request_ids)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_row_action_and_detail_request_aliases_for_wave7_operator_coverage(self) -> None:
        stdout = io.StringIO()

        call_command("membership_committee_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        request_aliases = payload["actors"]["committee_reviewer"]["request_aliases"]

        self.assertTrue(
            {
                "pending_row_approve",
                "pending_row_reject",
                "pending_row_rfi",
                "pending_row_action",
                "on_hold_row_approve",
            }.issubset(set(request_aliases.keys()))
        )

        detail_request = MembershipRequest.objects.get(pk=request_aliases["pending_row_action"])
        self.assertEqual(detail_request.status, MembershipRequest.Status.pending)
        self.assertEqual(detail_request.membership_type_id, "mirror")

        on_hold_request = MembershipRequest.objects.get(pk=request_aliases["on_hold_row_approve"])
        self.assertEqual(on_hold_request.status, MembershipRequest.Status.on_hold)
        self.assertEqual(on_hold_request.membership_type_id, "mirror")

    @override_settings(
        ASTRA_E2E_MODE=True,
        ASTRA_E2E_FAKE_FREEIPA_ENABLED=True,
        MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["RU", "IR"],
        SELF_SERVICE_ADDRESS_COUNTRY_ATTR="fasstatusnote",
    )
    def test_command_seeds_detail_request_with_first_embargoed_country_warning_note(self) -> None:
        stdout = io.StringIO()

        with self.captureOnCommitCallbacks(execute=True):
            call_command("membership_committee_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        client = get_e2e_service_client()
        regular06 = client.user_show("regular06")["result"]
        self.assertEqual(regular06.get(country_attr_name()), ["RU"])

        request_id = payload["actors"]["committee_reviewer"]["request_aliases"]["pending_row_action"]
        detail_request = MembershipRequest.objects.get(pk=request_id)
        warning_text = (
            f"This user's declared country, {country_label_from_code('RU')}, "
            "is on the list of embargoed countries."
        )
        self.assertTrue(
            Note.objects.filter(
                membership_request=detail_request,
                username=CUSTOS,
                content=warning_text,
            ).exists()
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_emits_workflow_examples_with_canonical_audit_history(self) -> None:
        stdout = io.StringIO()

        call_command("membership_committee_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        workflow_examples = payload["workflow_examples"]
        self.assertEqual(
            set(workflow_examples.keys()),
            {"accepted", "rejected", "ignored", "rfi_followup_review"},
        )

        accepted_request = MembershipRequest.objects.get(pk=workflow_examples["accepted"]["request_id"])
        rejected_request = MembershipRequest.objects.get(pk=workflow_examples["rejected"]["request_id"])
        ignored_request = MembershipRequest.objects.get(pk=workflow_examples["ignored"]["request_id"])
        rfi_followup_request = MembershipRequest.objects.get(pk=workflow_examples["rfi_followup_review"]["request_id"])

        self.assertEqual(accepted_request.status, MembershipRequest.Status.approved)
        self.assertEqual(rejected_request.status, MembershipRequest.Status.rejected)
        self.assertEqual(ignored_request.status, MembershipRequest.Status.ignored)
        self.assertEqual(rfi_followup_request.status, MembershipRequest.Status.pending)
        self.assertIsNone(rfi_followup_request.on_hold_at)

        self.assertEqual(
            list(
                MembershipLog.objects.filter(membership_request=accepted_request)
                .order_by("created_at", "pk")
                .values_list("action", flat=True)
            ),
            [MembershipLog.Action.requested, MembershipLog.Action.approved],
        )
        self.assertEqual(
            list(
                MembershipLog.objects.filter(membership_request=rejected_request)
                .order_by("created_at", "pk")
                .values_list("action", flat=True)
            ),
            [MembershipLog.Action.requested, MembershipLog.Action.rejected],
        )
        self.assertEqual(
            list(
                MembershipLog.objects.filter(membership_request=ignored_request)
                .order_by("created_at", "pk")
                .values_list("action", flat=True)
            ),
            [MembershipLog.Action.requested, MembershipLog.Action.ignored],
        )
        self.assertEqual(
            list(
                MembershipLog.objects.filter(membership_request=rfi_followup_request)
                .order_by("created_at", "pk")
                .values_list("action", flat=True)
            ),
            [
                MembershipLog.Action.requested,
                MembershipLog.Action.on_hold,
                MembershipLog.Action.resubmitted,
            ],
        )

        self.assertEqual(
            Note.objects.filter(
                membership_request=accepted_request,
                action__type="vote",
            ).count(),
            2,
        )
        self.assertEqual(
            Note.objects.filter(
                membership_request=rejected_request,
                action__type="vote",
            ).count(),
            2,
        )
        self.assertEqual(
            Note.objects.filter(
                membership_request=ignored_request,
                action__type="vote",
            ).count(),
            2,
        )
        self.assertEqual(
            Note.objects.filter(
                membership_request=rfi_followup_request,
                action__type="vote",
            ).count(),
            2,
        )
        self.assertTrue(
            Note.objects.filter(
                membership_request=rfi_followup_request,
                action__type="request_resubmitted",
            ).exists()
        )