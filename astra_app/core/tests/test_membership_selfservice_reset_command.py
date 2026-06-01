import io
import json
from datetime import datetime

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import get_e2e_service_client
from core.models import (
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Note,
    Organization,
)
from core.tests.utils_test_data import ensure_core_categories


class MembershipSelfServiceResetCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        ensure_core_categories()

    @override_settings(ASTRA_E2E_MODE=False, ASTRA_E2E_FAKE_FREEIPA_ENABLED=False)
    def test_command_rejects_runs_outside_fake_freeipa_e2e_mode(self) -> None:
        with self.assertRaisesMessage(CommandError, "ASTRA_E2E_FAKE_FREEIPA_ENABLED"):
            call_command("membership_selfservice_reset")

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_wave1_membership_self_service_scenarios_idempotently(self) -> None:
        stdout_first = io.StringIO()
        stdout_second = io.StringIO()

        call_command("membership_selfservice_reset", stdout=stdout_first)
        first_payload = json.loads(stdout_first.getvalue())

        call_command("membership_selfservice_reset", stdout=stdout_second)
        second_payload = json.loads(stdout_second.getvalue())

        self.assertEqual(first_payload["scenario"], "membership-self-service")
        self.assertEqual(second_payload["scenario"], "membership-self-service")
        self.assertEqual(
            set(first_payload["actors"].keys()),
            {"regular01", "regular32", "regular33", "regular34", "regular35", "regular36"},
        )
        self.assertEqual(
            set(second_payload["actors"].keys()),
            {"regular01", "regular32", "regular33", "regular34", "regular35", "regular36"},
        )

        self.assertTrue(MembershipType.objects.get(code="individual").enabled)
        self.assertTrue(MembershipType.objects.get(code="mirror").enabled)
        mirror_category = MembershipTypeCategory.objects.get(name="mirror")
        self.assertTrue(mirror_category.is_individual)
        self.assertTrue(mirror_category.is_organization)

        self.assertFalse(
            MembershipRequest.objects.filter(
                requested_username="regular01",
                membership_type_id="individual",
            ).exists()
        )

        duplicate_request = MembershipRequest.objects.get(
            requested_username="regular32",
            membership_type_id="individual",
        )
        self.assertEqual(duplicate_request.status, MembershipRequest.Status.pending)

        self.assertTrue(
            Membership.objects.filter(
                target_username="regular33",
                membership_type_id="mirror",
            ).exists()
        )
        self.assertEqual(
            MembershipRequest.objects.filter(
                requested_username="regular33",
                membership_type_id="mirror",
                status__in=[MembershipRequest.Status.pending, MembershipRequest.Status.on_hold],
            ).count(),
            0,
        )
        approved_request = MembershipRequest.objects.get(
            requested_username="regular33",
            membership_type_id="mirror",
            status=MembershipRequest.Status.approved,
        )
        self.assertTrue(any("Domain" in response for response in approved_request.responses))
        self.assertTrue(any("Pull request" in response for response in approved_request.responses))

        on_hold_request = MembershipRequest.objects.get(
            requested_username="regular34",
            membership_type_id="mirror",
        )
        self.assertEqual(on_hold_request.status, MembershipRequest.Status.on_hold)

        pending_request = MembershipRequest.objects.get(
            requested_username="regular35",
            membership_type_id="individual",
        )
        self.assertEqual(pending_request.status, MembershipRequest.Status.pending)

        self.assertEqual(set(first_payload["actors"]["regular34"]["request_aliases"].keys()), {"resubmit_on_hold"})
        self.assertEqual(set(first_payload["actors"]["regular35"]["request_aliases"].keys()), {"rescind_pending"})
        self.assertEqual(set(second_payload["actors"]["regular34"]["request_aliases"].keys()), {"resubmit_on_hold"})
        self.assertEqual(set(second_payload["actors"]["regular35"]["request_aliases"].keys()), {"rescind_pending"})
        self.assertEqual(set(first_payload["actors"]["regular36"]["request_aliases"].keys()), {"rfi_followup_review"})
        self.assertEqual(set(second_payload["actors"]["regular36"]["request_aliases"].keys()), {"rfi_followup_review"})

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_signs_required_coc_agreement_for_wave1_actors(self) -> None:
        agreement = FreeIPAFASAgreement.create(
            "AlmaLinux Community Code of Conduct",
            description="Seeded shared agreement for coexistence coverage.",
        )
        agreement.add_user("shared-existing-user")

        call_command("membership_selfservice_reset")

        agreement = FreeIPAFASAgreement.get("AlmaLinux Community Code of Conduct")

        self.assertIsNotNone(agreement)
        self.assertTrue(
            {
                "regular01",
                "regular32",
                "regular33",
                "regular34",
                "regular35",
                "regular36",
            }.issubset(set(agreement.users))
        )
        self.assertIn("shared-existing-user", agreement.users)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_valid_country_codes_for_wave1_actors(self) -> None:
        call_command("membership_selfservice_reset")

        client = get_e2e_service_client()
        country_attr = settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR

        for username in ["regular01", "regular32", "regular33", "regular34", "regular35", "regular36"]:
            self.assertEqual(client.user_show(username)["result"].get(country_attr), ["US"])

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_preserves_other_theme_requests_for_shared_full_suite_actors(self) -> None:
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
        auth_owner_request = MembershipRequest.objects.create(
            requested_username="regular03",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
            responses=[{"Contributions": "Auth profile pending request seed."}],
        )
        committee_shell_request = MembershipRequest.objects.create(
            requested_username="regular02",
            membership_type_id="mirror",
            status=MembershipRequest.Status.pending,
            responses=[{"Domain": "https://mirror-shell.example.test"}],
        )
        committee_renewal_request = MembershipRequest.objects.create(
            requested_username="regular04",
            membership_type_id="mirror",
            status=MembershipRequest.Status.pending,
            responses=[{"Domain": "https://mirror-renewal.example.test"}],
        )
        committee_detail_request = MembershipRequest.objects.create(
            requested_username="regular06",
            membership_type_id="mirror",
            status=MembershipRequest.Status.pending,
            responses=[{"Domain": "https://mirror-row-action.example.test"}],
        )
        committee_renewal_membership = Membership.objects.create(
            target_username="regular04",
            membership_type_id="mirror",
        )

        call_command("membership_selfservice_reset")

        self.assertTrue(MembershipRequest.objects.filter(pk=auth_owner_request.pk).exists())
        self.assertTrue(MembershipRequest.objects.filter(pk=committee_shell_request.pk).exists())
        self.assertTrue(MembershipRequest.objects.filter(pk=committee_renewal_request.pk).exists())
        self.assertTrue(MembershipRequest.objects.filter(pk=committee_detail_request.pk).exists())
        self.assertTrue(Membership.objects.filter(pk=committee_renewal_membership.pk).exists())

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_preserves_existing_sponsorship_type_display_order(self) -> None:
        MembershipType.objects.update_or_create(
            code="ruby",
            defaults={
                "name": "Ruby Sponsor Member",
                "group_cn": "almalinux-ruby",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "group_cn": "almalinux-silver",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

        call_command("membership_selfservice_reset")

        self.assertEqual(MembershipType.objects.get(code="ruby").sort_order, 0)
        self.assertEqual(MembershipType.objects.get(code="gold").sort_order, 1)
        self.assertEqual(MembershipType.objects.get(code="silver").sort_order, 2)

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_settings_actor_into_the_mirror_freeipa_group_for_leave_submission(self) -> None:
        call_command("membership_selfservice_reset")

        client = get_e2e_service_client()

        self.assertIn(
            "almalinux-mirror",
            client.user_show("regular33")["result"].get("memberof_group", []),
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_emits_routes_request_aliases_and_settings_membership_contract(self) -> None:
        stdout = io.StringIO()

        call_command("membership_selfservice_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        self.assertEqual(payload["routes"]["create"], "/membership/request/")
        self.assertEqual(payload["routes"]["settings_membership"]["regular33"], "/settings/?tab=membership")
        self.assertEqual(payload["routes"]["profiles"]["regular34"], "/user/regular34/")

        requests = payload["requests"]
        self.assertEqual(requests["duplicate_pending"]["actor_username"], "regular32")
        self.assertEqual(requests["duplicate_pending"]["browser_state"], "pending")
        self.assertRegex(requests["duplicate_pending"]["detail_route"], r"^/membership/request/\d+/$")
        self.assertEqual(requests["resubmit_on_hold"]["actor_username"], "regular34")
        self.assertEqual(requests["resubmit_on_hold"]["browser_state"], "on_hold")
        self.assertEqual(requests["rescind_pending"]["actor_username"], "regular35")
        self.assertEqual(requests["rescind_pending"]["browser_state"], "pending")

        settings_membership = payload["settings"]["membership"]
        self.assertEqual(settings_membership["actor_username"], "regular33")
        self.assertEqual(settings_membership["route"], "/settings/?tab=membership")
        self.assertEqual(settings_membership["active_membership_alias"], "regular03_active_mirror_membership")
        self.assertEqual(
            settings_membership["ordered_history_aliases"],
            [
                "regular03_history_expiry_changed",
                "regular03_history_approved",
                "regular03_history_requested",
            ],
        )
        self.assertEqual(settings_membership["active_membership"]["membership_type_code"], "mirror")
        self.assertEqual(settings_membership["active_membership"]["membership_type_name"], "Mirror")
        self.assertEqual(settings_membership["active_membership"]["terminate_membership_type_code"], "mirror")
        self.assertEqual(
            settings_membership["active_membership"]["terminate_route"],
            reverse("settings-membership-terminate", kwargs={"membership_type_code": "mirror"}),
        )
        self.assertEqual(
            settings_membership["history_rows"]["regular03_history_expiry_changed"]["action_label"],
            "Expiry changed",
        )
        self.assertEqual(
            settings_membership["history_rows"]["regular03_history_approved"]["action_label"],
            "Approved",
        )
        self.assertEqual(
            settings_membership["history_rows"]["regular03_history_requested"]["action_label"],
            "Requested",
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_emits_organization_target_routes_for_form_and_no_types_state(self) -> None:
        stdout = io.StringIO()

        call_command("membership_selfservice_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        organizations = payload["organizations"]
        self.assertEqual(set(organizations.keys()), {"representative_form_org", "representative_no_types_org"})

        form_org = organizations["representative_form_org"]
        no_types_org = organizations["representative_no_types_org"]

        self.assertEqual(form_org["representative_username"], "regular01")
        self.assertRegex(form_org["request_route"], r"^/organization/\d+/membership/request/$")
        self.assertRegex(form_org["detail_route"], r"^/organization/\d+/$")
        self.assertEqual(no_types_org["representative_username"], "regular32")
        self.assertRegex(no_types_org["request_route"], r"^/organization/\d+/membership/request/$")
        self.assertRegex(no_types_org["detail_route"], r"^/organization/\d+/$")

        regular01 = payload["actors"]["regular01"]
        regular02 = payload["actors"]["regular32"]
        self.assertEqual(regular01["organization_aliases"], {"representative_form_org": form_org["organization_id"]})
        self.assertEqual(regular02["organization_aliases"], {"representative_no_types_org": no_types_org["organization_id"]})

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_representative_org_request_form_and_no_types_state(self) -> None:
        call_command("membership_selfservice_reset")

        form_org = Organization.objects.get(name="Regular01 Sponsor Form Org")
        no_types_org = Organization.objects.get(name="Regular01 No Types Org")

        self.assertEqual(form_org.representative, "regular01")
        self.assertEqual(no_types_org.representative, "regular32")
        self.assertFalse(Membership.objects.filter(target_organization=form_org).exists())
        self.assertEqual(
            set(Membership.objects.filter(target_organization=no_types_org).values_list("membership_type_id", flat=True)),
            {"mirror", "silver"},
        )
        self.assertTrue(
            MembershipRequest.objects.filter(
                requested_organization=no_types_org,
                membership_type_id="gold",
                status=MembershipRequest.Status.pending,
            ).exists()
        )
        self.assertEqual(
            reverse("organization-membership-request", args=[form_org.pk]),
            f"/organization/{form_org.pk}/membership/request/",
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_emits_seeded_org_request_detail_alias_for_cross_link_coverage(self) -> None:
        stdout = io.StringIO()

        call_command("membership_selfservice_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        org_request = payload["requests"]["organization_target_pending"]
        organization = payload["organizations"]["representative_form_org"]

        self.assertEqual(org_request["actor_username"], "regular01")
        self.assertEqual(org_request["browser_state"], "pending")
        self.assertRegex(org_request["detail_route"], r"^/membership/request/\d+/$")
        self.assertEqual(org_request["target_kind"], "organization")
        self.assertEqual(org_request["target_organization_id"], organization["organization_id"])

        self.assertEqual(
            payload["actors"]["regular01"]["request_aliases"],
            {"organization_target_pending": org_request["request_id"]},
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_reserved_membership_history_aliases_in_declared_newest_first_order(self) -> None:
        stdout = io.StringIO()

        call_command("membership_selfservice_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())
        settings_membership = payload["settings"]["membership"]
        aliases = settings_membership["ordered_history_aliases"]
        created_at_values = [
            datetime.fromisoformat(settings_membership["history_rows"][alias]["created_at"].replace("Z", "+00:00"))
            for alias in aliases
        ]

        self.assertEqual(created_at_values, sorted(created_at_values, reverse=True))

        logs = list(
            MembershipLog.objects.filter(
                target_username="regular33",
                membership_type_id="mirror",
                action__in=["expiry_changed", "approved", "requested"],
            ).order_by("-created_at", "-pk")
        )
        self.assertEqual([log.action for log in logs[:3]], ["expiry_changed", "approved", "requested"])
        self.assertTrue(timezone.is_aware(logs[0].created_at))

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_clears_stale_settings_membership_termination_history(self) -> None:
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
        MembershipLog.objects.create(
            actor_username="regular33",
            target_username="regular33",
            membership_type_id="mirror",
            action=MembershipLog.Action.terminated,
        )

        call_command("membership_selfservice_reset")

        self.assertFalse(
            MembershipLog.objects.filter(
                target_username="regular33",
                membership_type_id="mirror",
                action=MembershipLog.Action.terminated,
            ).exists()
        )

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_seeds_rfi_followup_review_request_returned_to_pending_with_history(self) -> None:
        stdout = io.StringIO()

        call_command("membership_selfservice_reset", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        request_payload = payload["requests"]["rfi_followup_review"]
        membership_request = MembershipRequest.objects.get(pk=request_payload["request_id"])

        self.assertEqual(membership_request.status, MembershipRequest.Status.pending)
        self.assertIsNone(membership_request.on_hold_at)
        self.assertEqual(request_payload["browser_state"], MembershipRequest.Status.pending)
        self.assertEqual(request_payload["actor_username"], "regular36")
        self.assertEqual(
            list(
                MembershipLog.objects.filter(membership_request=membership_request)
                .order_by("created_at", "pk")
                .values_list("action", flat=True)
            ),
            [
                MembershipLog.Action.requested,
                MembershipLog.Action.on_hold,
                MembershipLog.Action.resubmitted,
            ],
        )
        self.assertTrue(
            Note.objects.filter(
                membership_request=membership_request,
                action__type="request_resubmitted",
            ).exists()
        )