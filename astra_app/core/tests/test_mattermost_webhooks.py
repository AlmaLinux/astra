import datetime
import json
import re
import warnings
from types import SimpleNamespace
from typing import override
from unittest.mock import Mock, patch

import requests
from django.core.exceptions import ValidationError
from django.template import Context
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.mattermost_webhooks import (
    _COMMON_TEMPLATE_VARIABLES,
    _EVENT_TEMPLATE_VARIABLES,
    _build_payload,
    _build_template_context,
    _condition_template,
    _default_payload,
    _post_to_endpoint,
    _render_template,
    condition_errors,
    dispatch_mattermost_event,
)
from core.membership import PendingRenewal
from core.models import (
    AccountDeletionRequest,
    AccountInvitation,
    Election,
    MattermostWebhookEndpoint,
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipTerminationFeedback,
    MembershipType,
    MembershipTypeCategory,
    Organization,
)
from core.operations import run_operations
from core.signals import CANONICAL_SIGNALS
from core.tokens import election_genesis_chain_hash


class MattermostWebhookModelValidationTests(SimpleTestCase):
    def test_clean_rejects_non_https_url(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="http://hooks.example.invalid/abc",
            events=["election_opened"],
        )

        with self.assertRaises(ValidationError):
            endpoint.clean()

    def test_clean_rejects_non_list_events(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events={"event": "election_opened"},
        )

        with self.assertRaises(ValidationError):
            endpoint.clean()

    def test_clean_warns_for_unknown_event_keys_but_allows_forward_compat(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_opened", "future_event_key"],
        )

        with warnings.catch_warnings(record=True) as captured_warnings:
            warnings.simplefilter("always")
            endpoint.clean()

        warning_messages = [str(item.message) for item in captured_warnings]
        self.assertTrue(
            any("Unknown event keys in MattermostWebhookEndpoint" in message for message in warning_messages)
        )

    def test_clean_rejects_non_string_event_items(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_opened", 123],
        )

        with self.assertRaises(ValidationError):
            endpoint.clean()


class MattermostWebhookTemplateAndPayloadTests(SimpleTestCase):
    def test_render_template_returns_empty_string_on_error_and_logs(self) -> None:
        with self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured:
            rendered = _render_template("{{ name|definitely_unknown_filter }}", {"name": "alice"})

        self.assertEqual(rendered, "")
        self.assertTrue(captured.records)

    def test_build_payload_applies_merge_order(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["membership_expired"],
            text="Custom text {{ count }}",
            attachments=[
                {
                    "title": "Override {{ count }} expired",
                    "fields": [{"title": "Count", "value": "{{ count }}", "short": True}],
                }
            ],
            props={"from_endpoint": "yes"},
            priority={"priority": "important", "requested_ack": False},
            channel="town-square",
            username="Astra Bot",
            icon_url="https://example.invalid/icon.png",
        )

        payload = _build_payload(
            endpoint=endpoint,
            event_key="membership_expired",
            kwargs={"count": 4},
        )

        self.assertEqual(payload["text"], "Custom text 4")
        self.assertEqual(payload["attachments"][0]["title"], "Override 4 expired")
        self.assertEqual(payload["attachments"][0]["fields"][0]["value"], "4")
        self.assertEqual(payload["props"]["from_endpoint"], "yes")
        self.assertEqual(payload["priority"]["priority"], "important")
        self.assertEqual(payload["channel"], "town-square")
        self.assertEqual(payload["username"], "Astra Bot")
        self.assertEqual(payload["icon_url"], "https://example.invalid/icon.png")

    def test_default_payload_uses_expected_color_for_event_family(self) -> None:
        payload = _default_payload(
            "membership_request_approved",
            {
                "actor": "alice",
                "membership_request": SimpleNamespace(
                    pk=1,
                    membership_type_id="sponsorship",
                    requested_username="alice",
                    organization_display_name="",
                ),
            },
        )
        self.assertEqual(payload["attachments"][0]["color"], "#36a64f")

    def test_build_payload_falls_back_on_bad_template(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_opened"],
            text="{{ unclosed",
        )

        payload = _build_payload(endpoint, "election_opened", {"actor": "test"})

        self.assertIn("text", payload)
        self.assertTrue(str(payload["text"]).strip())

    def test_build_payload_exposes_membership_request_details_in_template_context(self) -> None:
        membership_request = MembershipRequest(
            pk=42,
            requested_username="carol",
            membership_type=MembershipType(code="sponsorship", name="Sponsorship"),
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["membership_request_submitted"],
            text="type={{ membership_type_code }} name={{ membership_type_name }} target={{ membership_target_kind }}",
        )

        payload = _build_payload(
            endpoint,
            "membership_request_submitted",
            {"membership_request": membership_request, "actor": "alice"},
        )

        self.assertEqual(payload["text"], "type=sponsorship name=Sponsorship target=user")

    def test_membership_request_related_events_link_to_request_detail_page(self) -> None:
        membership_request = MembershipRequest(
            pk=42,
            requested_username="carol",
            membership_type=MembershipType(code="sponsorship", name="Sponsorship"),
        )
        expected_suffix = reverse("membership-request-detail", kwargs={"pk": membership_request.pk})
        event_keys = [
            "membership_request_submitted",
            "membership_request_approved",
            "membership_request_rejected",
            "membership_request_rescinded",
            "membership_rfi_sent",
            "membership_rfi_replied",
            "organization_membership_request_submitted",
            "organization_membership_request_approved",
            "organization_membership_request_rejected",
            "organization_membership_request_rescinded",
            "organization_membership_rfi_sent",
            "organization_membership_rfi_replied",
        ]

        for event_key in event_keys:
            kwargs: dict[str, object] = {
                "membership_request": membership_request,
                "actor": "alice",
            }
            if event_key.startswith("organization_membership_"):
                kwargs["organization_id"] = 123

            with self.subTest(event_key=event_key):
                payload = _default_payload(event_key, kwargs)
                self.assertTrue(str(payload["attachments"][0]["title_link"]).endswith(expected_suffix))

    def test_membership_request_payload_attachments_override_cannot_change_detail_link(self) -> None:
        membership_request = MembershipRequest(
            pk=42,
            requested_username="carol",
            membership_type=MembershipType(code="sponsorship", name="Sponsorship"),
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["membership_request_submitted"],
            attachments=[
                {
                    "title": "Custom attachment",
                    "title_link": "https://hooks.example.invalid/not-detail",
                }
            ],
        )

        payload = _build_payload(
            endpoint=endpoint,
            event_key="membership_request_submitted",
            kwargs={"membership_request": membership_request, "actor": "alice"},
        )

        expected_suffix = reverse("membership-request-detail", kwargs={"pk": membership_request.pk})
        self.assertTrue(str(payload["attachments"][0]["title_link"]).endswith(expected_suffix))

    def test_membership_request_payload_multi_attachments_override_all_keep_detail_link(self) -> None:
        membership_request = MembershipRequest(
            pk=42,
            requested_username="carol",
            membership_type=MembershipType(code="sponsorship", name="Sponsorship"),
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["membership_request_submitted"],
            attachments=[
                {
                    "title": "Custom attachment one",
                    "title_link": "https://hooks.example.invalid/not-detail-1",
                },
                {
                    "title": "Custom attachment two",
                    "title_link": "https://hooks.example.invalid/not-detail-2",
                },
            ],
        )

        payload = _build_payload(
            endpoint=endpoint,
            event_key="membership_request_submitted",
            kwargs={"membership_request": membership_request, "actor": "alice"},
        )

        expected_suffix = reverse("membership-request-detail", kwargs={"pk": membership_request.pk})
        self.assertEqual(len(payload["attachments"]), 2)
        for attachment in payload["attachments"]:
            self.assertTrue(str(attachment["title_link"]).endswith(expected_suffix))

    def test_build_payload_exposes_election_opened_genesis_and_end_datetime(self) -> None:
        election = Election(
            pk=7,
            name="Board 2026",
            end_datetime=datetime.datetime(2026, 5, 1, 12, 0, tzinfo=datetime.UTC),
            tally_result={},
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_opened"],
            text='genesis={{ election_genesis_hash }} end={{ election_end_datetime|date:"c" }}',
        )

        payload = _build_payload(endpoint, "election_opened", {"election": election, "actor": "alice"})

        self.assertIn(election_genesis_chain_hash(7), str(payload["text"]))
        self.assertIn("2026-05-01T12:00:00+00:00", str(payload["text"]))

    @patch("core.mattermost_webhooks.Ballot.objects.latest_chain_head_hash_for_election", return_value="deadbeef")
    def test_build_payload_exposes_election_closed_final_chain_hash(self, _chain_hash_mock: Mock) -> None:
        election = Election(
            pk=8,
            name="Board 2026",
            end_datetime=datetime.datetime(2026, 5, 1, 12, 0, tzinfo=datetime.UTC),
            tally_result={},
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_closed"],
            text="final={{ election_final_chain_hash }}",
        )

        payload = _build_payload(endpoint, "election_closed", {"election": election, "actor": "alice"})

        self.assertEqual(payload["text"], "final=deadbeef")

    def test_build_payload_exposes_election_tallied_winners(self) -> None:
        election = Election(
            pk=9,
            name="Board 2026",
            end_datetime=datetime.datetime(2026, 5, 1, 12, 0, tzinfo=datetime.UTC),
            tally_result={"elected": ["alice", "bob"]},
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["election_tallied"],
            text="winners={{ election_winners|join:', ' }}",
        )

        payload = _build_payload(endpoint, "election_tallied", {"election": election, "actor": "alice"})

        self.assertEqual(payload["text"], "winners=alice, bob")

    def test_payload_organization_country_changed(self) -> None:
        org = SimpleNamespace(pk=123, name="Example Org", representative="")
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["organization_country_changed"],
        )

        payload = _build_payload(
            endpoint,
            "organization_country_changed",
            {
                "organization": org,
                "old_country": "US",
                "new_country": "DE",
                "actor": "alice",
            },
        )

        self.assertEqual(payload["text"], "Organization country changed")

        fields = payload["attachments"][0]["fields"]
        by_title = {str(field.get("title")): str(field.get("value")) for field in fields}
        self.assertEqual(by_title.get("Old country"), "US")
        self.assertEqual(by_title.get("New country"), "DE")

    def test_payload_user_country_changed(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["user_country_changed"],
        )

        payload = _build_payload(
            endpoint,
            "user_country_changed",
            {
                "username": "carol",
                "old_country": "US",
                "new_country": "FR",
                "actor": "carol",
            },
        )

        self.assertEqual(payload["text"], "User country changed")

        fields = payload["attachments"][0]["fields"]
        by_title = {str(field.get("title")): str(field.get("value")) for field in fields}
        self.assertEqual(by_title.get("Username"), "carol")
        self.assertEqual(by_title.get("Old country"), "US")
        self.assertEqual(by_title.get("New country"), "FR")

    def test_payload_user_embargoed_country_changed(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Compliance",
            url="https://hooks.example.invalid/abc",
            events=["user_embargoed_country_changed"],
        )

        payload = _build_payload(
            endpoint,
            "user_embargoed_country_changed",
            {
                "username": "carol",
                "old_country": "US",
                "new_country": "IR",
                "actor": "carol",
            },
        )

        self.assertEqual(payload["text"], "User country changed to/from embargoed country")
        fields = payload["attachments"][0]["fields"]
        by_title = {str(field.get("title")): str(field.get("value")) for field in fields}
        self.assertEqual(by_title.get("Username"), "carol")
        self.assertEqual(by_title.get("Old country"), "US")
        self.assertEqual(by_title.get("New country"), "IR")
        self.assertEqual(by_title.get("Actor"), "carol")

    def test_payload_organization_embargoed_country_changed(self) -> None:
        organization = SimpleNamespace(pk=123, name="Example Org", representative="")
        endpoint = MattermostWebhookEndpoint(
            label="Compliance",
            url="https://hooks.example.invalid/abc",
            events=["organization_embargoed_country_changed"],
        )

        payload = _build_payload(
            endpoint,
            "organization_embargoed_country_changed",
            {
                "organization": organization,
                "old_country": "IR",
                "new_country": "DE",
                "actor": "alice",
            },
        )

        self.assertEqual(payload["text"], "Organization country changed to/from embargoed country")
        fields = payload["attachments"][0]["fields"]
        by_title = {str(field.get("title")): str(field.get("value")) for field in fields}
        self.assertEqual(by_title.get("Organization"), "Example Org")
        self.assertEqual(by_title.get("Old country"), "IR")
        self.assertEqual(by_title.get("New country"), "DE")
        self.assertEqual(by_title.get("Actor"), "alice")

    def test_build_payload_account_invitation_accepted_exposes_invitation_context_and_link(self) -> None:
        invitation = AccountInvitation(
            pk=42,
            email="invitee@example.com",
            full_name="Invitee",
            accepted_username="alice",
            freeipa_matched_usernames=["alice", "ally"],
            organization=Organization(pk=7, name="Example Org"),
        )
        endpoint = MattermostWebhookEndpoint(
            label="Ops",
            url="https://hooks.example.invalid/abc",
            events=["account_invitation_accepted"],
            text=(
                "invite={{ account_invitation_email }} accepted={{ username }} "
                'matches={{ account_invitation_matched_usernames|join:", " }} org={{ organization_name }}'
            ),
        )

        payload = _build_payload(
            endpoint,
            "account_invitation_accepted",
            {"account_invitation": invitation, "actor": "committee"},
        )

        self.assertEqual(
            payload["text"],
            "invite=invitee@example.com accepted=alice matches=alice, ally org=Example Org",
        )
        self.assertTrue(str(payload["attachments"][0]["title_link"]).endswith(reverse("account-invitations")))
        fields = payload["attachments"][0]["fields"]
        by_title = {str(field.get("title")): str(field.get("value")) for field in fields}
        self.assertEqual(by_title.get("Email"), "invitee@example.com")
        self.assertEqual(by_title.get("Accepted username"), "alice")
        self.assertEqual(by_title.get("Organization"), "Example Org")

    def test_build_payload_account_deletion_requested_exposes_request_context_without_reason_text(self) -> None:
        deletion_request = SimpleNamespace(
            pk=42,
            username="alice",
            status="pending_review",
            manual_review_required=True,
            blocker_codes=["organization_representative", "open_election"],
            reason_text="This must never appear in Mattermost.",
        )
        endpoint = MattermostWebhookEndpoint(
            label="Privacy",
            url="https://hooks.example.invalid/privacy",
            events=["account_deletion_requested"],
            text=(
                "request={{ account_deletion_request_id }} user={{ username }} "
                "status={{ account_deletion_status }} manual={{ account_deletion_manual_review_required }} "
                'blockers={{ account_deletion_blocker_codes|join:", " }}'
            ),
        )

        payload = _build_payload(
            endpoint,
            "account_deletion_requested",
            {"account_deletion_request": deletion_request, "actor": "alice"},
        )

        self.assertEqual(
            payload["text"],
            "request=42 user=alice status=pending_review manual=True blockers=organization_representative, open_election",
        )
        self.assertNotIn("This must never appear in Mattermost.", str(payload))

    def test_build_payload_account_deletion_completed_exposes_request_context_without_reason_text(self) -> None:
        deletion_request = SimpleNamespace(
            pk=42,
            username="alice",
            status="completed",
            manual_review_required=False,
            blocker_codes=[],
            reason_text="This must never appear in Mattermost.",
        )
        endpoint = MattermostWebhookEndpoint(
            label="Privacy",
            url="https://hooks.example.invalid/privacy",
            events=["account_deletion_completed"],
            text=(
                "request={{ account_deletion_request_id }} user={{ username }} "
                "status={{ account_deletion_status }} manual={{ account_deletion_manual_review_required }}"
            ),
        )

        payload = _build_payload(
            endpoint,
            "account_deletion_completed",
            {"account_deletion_request": deletion_request, "actor": "admin"},
        )

        self.assertEqual(
            payload["text"],
            "request=42 user=alice status=completed manual=False",
        )
        self.assertEqual(payload["attachments"][0]["title"], "Deletion request 42")
        self.assertTrue(
            payload["attachments"][0]["title_link"].endswith(
                reverse("admin:core_accountdeletionrequest_change", args=[42])
            )
        )
        self.assertNotIn("This must never appear in Mattermost.", str(payload))

    def test_build_payload_account_deletion_uses_request_id_for_admin_change_link_without_object(self) -> None:
        payload = _default_payload(
            "account_deletion_completed",
            {
                "account_deletion_request_id": 42,
                "username": "alice",
                "account_deletion_status": "completed",
                "account_deletion_manual_review_required": False,
                "account_deletion_blocker_codes": [],
                "actor": "admin",
            },
        )

        self.assertTrue(
            payload["attachments"][0]["title_link"].endswith(
                reverse("admin:core_accountdeletionrequest_change", args=[42])
            )
        )

    def test_build_payload_account_deletion_without_request_uses_admin_changelist_link(self) -> None:
        payload = _default_payload(
            "account_deletion_completed",
            {
                "username": "alice",
                "account_deletion_status": "completed",
                "account_deletion_manual_review_required": False,
                "account_deletion_blocker_codes": [],
                "actor": "admin",
            },
        )

        self.assertTrue(
            payload["attachments"][0]["title_link"].endswith(
                reverse("admin:core_accountdeletionrequest_changelist")
            )
        )


class SponsorshipMattermostPayloadTests(SimpleTestCase):
    endpoint = MattermostWebhookEndpoint(label="Sponsors", url="https://hooks.example.invalid/sponsors")

    def _sponsorship(self, organization_id: int = 5, name: str = "Acme Corp", representative: str = "jdoe") -> object:
        return SimpleNamespace(
            target_organization=SimpleNamespace(pk=organization_id, name=name, representative=representative),
            membership_type=SimpleNamespace(
                code="gold", name="Gold Sponsor", category_id="sponsorship", annual_dues_usd=5000
            ),
            dues_usd=None,
            expires_at=datetime.datetime(2026, 10, 29, 23, 59, 59, tzinfo=datetime.UTC),
        )

    def _org_link(self, organization_id: int = 5) -> str:
        return reverse("organization-detail", kwargs={"organization_id": organization_id})

    def test_expiring_soon_names_the_org_and_links_to_it(self) -> None:
        payload = _build_payload(
            self.endpoint,
            "organization_membership_expiring_soon",
            {"membership": self._sponsorship(), "days": 11, "sponsor_notified": True},
        )

        self.assertEqual(payload["text"], "Sponsorship for Acme Corp expires in 11 days")
        attachment = payload["attachments"][0]
        self.assertEqual(attachment["color"], "#ff8c00")
        self.assertEqual(attachment["title"], "Acme Corp")
        self.assertTrue(attachment["title_link"].endswith(self._org_link()))
        self.assertEqual(
            attachment["fields"],
            [
                {"title": "Level", "value": "Gold Sponsor", "short": True},
                {"title": "Expires", "value": "2026-10-29", "short": True},
                {"title": "Representative", "value": "jdoe", "short": True},
                {"title": "Annual dues", "value": "$5,000", "short": True},
                {"title": "Sponsor notified", "value": "Yes", "short": True},
            ],
        )

    def test_expiring_soon_flags_a_sponsor_nobody_could_email(self) -> None:
        payload = _build_payload(
            self.endpoint,
            "organization_membership_expiring_soon",
            {"membership": self._sponsorship(representative=""), "days": 1, "sponsor_notified": False},
        )

        self.assertEqual(payload["text"], "Sponsorship for Acme Corp expires in 1 day")
        fields = {field["title"]: field["value"] for field in payload["attachments"][0]["fields"]}
        self.assertEqual(fields["Sponsor notified"], "**No**")
        self.assertNotIn("Representative", fields)

    def test_expired_names_the_org_and_reports_failed_cleanup(self) -> None:
        removed = _build_payload(
            self.endpoint,
            "organization_membership_expired",
            {"membership": self._sponsorship(), "sponsor_notified": True, "cleanup_failed": False},
        )
        failed = _build_payload(
            self.endpoint,
            "organization_membership_expired",
            {"membership": self._sponsorship(), "sponsor_notified": False, "cleanup_failed": True},
        )

        self.assertEqual(removed["text"], "Sponsorship for Acme Corp expired")
        self.assertEqual(
            failed["text"],
            "Sponsorship for Acme Corp expired, but removing its FreeIPA access failed; retrying tomorrow",
        )
        for payload in (removed, failed):
            attachment = payload["attachments"][0]
            self.assertEqual(attachment["color"], "#cc0000")
            self.assertEqual(attachment["title"], "Acme Corp")
            self.assertTrue(attachment["title_link"].endswith(self._org_link()))

    def test_endpoint_text_can_use_sponsorship_template_variables(self) -> None:
        endpoint = MattermostWebhookEndpoint(
            label="Sponsors",
            url="https://hooks.example.invalid/sponsors",
            text="{{ organization_name }} ({{ membership_type_name }}, rep {{ representative }}) in {{ days }}d",
        )

        payload = _build_payload(
            endpoint,
            "organization_membership_expiring_soon",
            {"membership": self._sponsorship(), "days": 45, "sponsor_notified": True},
        )

        self.assertEqual(payload["text"], "Acme Corp (Gold Sponsor, rep jdoe) in 45d")

    def test_renewals_pending_lists_each_org_with_its_request(self) -> None:
        now = timezone.now()
        renewals = [
            SimpleNamespace(
                membership=self._sponsorship(organization_id=5, name="Acme Corp"),
                membership_request=SimpleNamespace(pk=12, status="on_hold"),
                term_ended_at=now - datetime.timedelta(days=18, hours=1),
            ),
            SimpleNamespace(
                membership=self._sponsorship(organization_id=7, name="Initech"),
                membership_request=SimpleNamespace(pk=31, status="pending"),
                term_ended_at=now - datetime.timedelta(days=1, hours=1),
            ),
        ]

        payload = _build_payload(
            self.endpoint, "organization_membership_renewals_pending", {"count": 2, "renewals": renewals}
        )

        self.assertEqual(payload["text"], "2 sponsorship renewals pending past expiry")
        attachment = payload["attachments"][0]
        self.assertEqual(attachment["color"], "#ff8c00")
        self.assertTrue(attachment["title_link"].endswith(reverse("membership-requests")))
        rows = attachment["text"].splitlines()
        self.assertEqual(rows[0], "| Organization | Level | Term ended | Overdue | Request |")
        acme_term_ended = renewals[0].term_ended_at.date().isoformat()
        self.assertRegex(
            rows[2],
            rf"^\| \[Acme Corp\]\(\S*{self._org_link(5)}\) \| Gold Sponsor \| {acme_term_ended} \| 18 days \| "
            rf"\[#12\]\(\S*{reverse('membership-request-detail', kwargs={'pk': 12})}\) On Hold \|$",
        )
        self.assertRegex(rows[3], r"^\| \[Initech\].* \| 1 day \| \[#31\].* Pending \|$")

    def test_renewals_pending_singular_text(self) -> None:
        renewal = SimpleNamespace(
            membership=self._sponsorship(),
            membership_request=SimpleNamespace(pk=12, status="pending"),
            term_ended_at=timezone.now() - datetime.timedelta(days=3),
        )

        payload = _build_payload(
            self.endpoint, "organization_membership_renewals_pending", {"count": 1, "renewals": [renewal]}
        )

        self.assertEqual(payload["text"], "1 sponsorship renewal pending past expiry")


class MattermostWebhookTemplateVariableRegistryTests(TestCase):
    """Conditions are validated against the registry, so it must match the context dispatch builds."""

    def _event_kwargs(self) -> dict[str, dict[str, object]]:
        now = timezone.now()
        individual = MembershipType(code="individual", name="Individual", category_id="individual")
        gold = MembershipType(code="gold", name="Gold Sponsor", category_id="sponsorship", annual_dues_usd=5000)
        organization = Organization(pk=5, name="Acme Corp", representative="jdoe")
        org_request = MembershipRequest(pk=43, requested_organization=organization, membership_type=gold)
        sponsorship = Membership(target_organization=organization, membership_type=gold, expires_at=now)
        election = Election(pk=1, name="Board", end_datetime=now, tally_result={"elected": ["alice"]})
        account_deletion = {
            "account_deletion_request": AccountDeletionRequest(pk=9, username="bob", status="pending_review"),
            "actor": "bob",
        }

        user_request_kwargs = {
            "membership_request": MembershipRequest(pk=42, requested_username="carol", membership_type=individual),
            "actor": "alice",
        }
        org_request_kwargs = {"membership_request": org_request, "actor": "alice", "organization_id": 5}
        user_country = {"username": "carol", "old_country": "US", "new_country": "CA", "actor": "carol"}
        org_country = {"organization": organization, "old_country": "US", "new_country": "CA", "actor": "jdoe"}

        return {
            "account_invitation_accepted": {
                "account_invitation": AccountInvitation(
                    pk=3,
                    email="dave@example.com",
                    full_name="Dave",
                    accepted_username="dave",
                    accepted_at=now,
                    freeipa_matched_usernames=["dave"],
                    organization=organization,
                ),
                "actor": "dave",
            },
            "election_opened": {"election": election, "actor": "alice"},
            "election_closed": {"election": election, "actor": "alice"},
            "election_tallied": {"election": election, "actor": "alice"},
            "election_deadline_extended": {
                "election": election,
                "actor": "alice",
                "previous_end_datetime": now,
                "new_end_datetime": now,
            },
            "election_quorum_met": {"election": election},
            "membership_request_submitted": user_request_kwargs,
            "membership_request_approved": user_request_kwargs,
            "membership_request_rejected": user_request_kwargs,
            "membership_request_rescinded": user_request_kwargs,
            "membership_rfi_sent": user_request_kwargs,
            "membership_rfi_replied": user_request_kwargs,
            "membership_expiring_soon": {"count": 3},
            "membership_expired": {"count": 3},
            "membership_self_terminated": {
                "actor": "carol",
                "username": "carol",
                "membership_type": individual,
                "termination_feedback": MembershipTerminationFeedback(pk=7, reason_category="privacy"),
            },
            "account_deletion_requested": account_deletion,
            "account_deletion_pending_privilege_check": account_deletion,
            "account_deletion_approved": account_deletion,
            "account_deletion_rejected": account_deletion,
            "account_deletion_cancelled": account_deletion,
            "account_deletion_completed": account_deletion,
            "organization_membership_request_submitted": org_request_kwargs,
            "organization_membership_request_approved": org_request_kwargs,
            "organization_membership_request_rejected": org_request_kwargs,
            "organization_membership_request_rescinded": org_request_kwargs,
            "organization_membership_rfi_sent": org_request_kwargs,
            "organization_membership_rfi_replied": org_request_kwargs,
            "organization_membership_expiring_soon": {"membership": sponsorship, "days": 7, "sponsor_notified": True},
            "organization_membership_expired": {
                "membership": sponsorship,
                "sponsor_notified": True,
                "cleanup_failed": False,
            },
            "organization_membership_renewals_pending": {
                "count": 1,
                "renewals": [PendingRenewal(membership=sponsorship, membership_request=org_request, term_ended_at=now)],
            },
            "organization_claimed": {"organization": organization, "actor": "jdoe"},
            "organization_created": {"organization": organization, "actor": "jdoe"},
            "user_country_changed": user_country,
            "user_embargoed_country_changed": user_country,
            "organization_country_changed": org_country,
            "organization_embargoed_country_changed": org_country,
        }

    def test_registry_lists_exactly_the_variables_each_event_exposes(self) -> None:
        event_kwargs = self._event_kwargs()
        self.assertEqual(set(event_kwargs), set(CANONICAL_SIGNALS))

        for event_key, kwargs in event_kwargs.items():
            with self.subTest(event_key=event_key):
                context = _build_template_context(event_key, kwargs)
                self.assertEqual(
                    set(context) - set(_COMMON_TEMPLATE_VARIABLES),
                    set(_EVENT_TEMPLATE_VARIABLES[event_key]) - set(_COMMON_TEMPLATE_VARIABLES),
                )

    def test_membership_events_expose_the_membership_type_category(self) -> None:
        event_kwargs = self._event_kwargs()

        for event_key, expected in [
            ("membership_request_submitted", "individual"),
            ("organization_membership_request_submitted", "sponsorship"),
            ("organization_membership_expiring_soon", "sponsorship"),
            ("membership_self_terminated", "individual"),
        ]:
            with self.subTest(event_key=event_key):
                context = _build_template_context(event_key, event_kwargs[event_key])
                self.assertEqual(context["membership_type_category"], expected)

    def test_context_links_to_the_pages_of_what_it_names(self) -> None:
        # Templates cannot reverse URLs, so links in a custom message must come from the context.
        event_kwargs = self._event_kwargs()

        for event_key, expected_paths in [
            (
                "membership_request_submitted",
                {
                    "membership_request_url": reverse("membership-request-detail", kwargs={"pk": 42}),
                    "user_url": reverse("user-profile", kwargs={"username": "carol"}),
                },
            ),
            (
                "organization_membership_request_submitted",
                {"organization_url": reverse("organization-detail", kwargs={"organization_id": 5})},
            ),
            (
                "election_opened",
                {
                    "election_url": reverse("election-detail", kwargs={"election_id": 1}),
                    "election_vote_url": reverse("election-vote", args=[1]),
                },
            ),
            ("account_invitation_accepted", {"user_url": reverse("user-profile", kwargs={"username": "dave"})}),
        ]:
            with self.subTest(event_key=event_key):
                context = _build_template_context(event_key, event_kwargs[event_key])
                for name, path in expected_paths.items():
                    self.assertTrue(str(context[name]).endswith(path), (name, context[name]))

    def test_membership_type_is_the_model_wherever_the_type_is_known(self) -> None:
        event_kwargs = self._event_kwargs()

        for event_key in (
            "membership_request_submitted",
            "organization_membership_expired",
            "membership_self_terminated",
        ):
            with self.subTest(event_key=event_key):
                context = _build_template_context(event_key, event_kwargs[event_key])
                self.assertIsInstance(context["membership_type"], MembershipType)

    def test_org_request_for_a_deleted_organization_keeps_its_name(self) -> None:
        request = MembershipRequest(
            pk=44,
            deleted_organization_id=5,
            deleted_organization_name="Gone Corp",
            membership_type=MembershipType(code="gold", name="Gold", category_id="sponsorship"),
        )

        context = _build_template_context(
            "organization_membership_request_submitted", {"membership_request": request, "organization_id": None}
        )

        self.assertEqual(context["organization_name"], "Gone Corp")
        self.assertIsNone(context.get("organization_id"))
        self.assertNotIn("organization", context)

    def test_events_name_their_user_and_organization_the_same_way(self) -> None:
        event_kwargs = self._event_kwargs()
        acme = {"organization_id": 5, "organization_name": "Acme Corp"}

        for event_key, expected in [
            ("membership_request_submitted", {"username": "carol"}),
            ("membership_self_terminated", {"username": "carol"}),
            ("user_country_changed", {"username": "carol"}),
            ("account_deletion_requested", {"username": "bob"}),
            ("account_invitation_accepted", {"username": "dave", **acme}),
            ("organization_membership_request_submitted", acme),
            ("organization_membership_expired", acme),
            ("organization_created", acme),
            ("organization_country_changed", acme),
        ]:
            with self.subTest(event_key=event_key):
                context = _build_template_context(event_key, event_kwargs[event_key])
                self.assertEqual({name: context.get(name) for name in expected}, expected)


class MattermostWebhookConditionValidationTests(TestCase):
    sponsorship_events = ["organization_membership_expiring_soon", "organization_membership_expired"]

    @override
    def setUp(self) -> None:
        super().setUp()
        MembershipTypeCategory.objects.update_or_create(pk="sponsorship", defaults={"is_organization": True})
        for code in ("gold", "platinum"):
            MembershipType.objects.update_or_create(
                code=code, defaults={"name": code.title(), "category_id": "sponsorship"}
            )

    def _single_error(self, condition: str, events: list[str]) -> str:
        errors = condition_errors(condition, events)
        self.assertEqual(len(errors), 1, errors)
        return errors[0]

    def test_valid_condition_has_no_errors(self) -> None:
        self.assertEqual(
            condition_errors(
                'membership_type_category == "sponsorship" and membership_type_code != "gold" and not sponsor_notified',
                self.sponsorship_events,
            ),
            [],
        )

    def test_reports_syntax_errors(self) -> None:
        error = self._single_error("membership_type_code ==", self.sponsorship_events)

        self.assertIn("Unexpected end of expression", error)

    def test_rejects_template_tags_so_the_field_holds_only_one_condition(self) -> None:
        for condition in [
            'membership_type_code == "gold" %}{% if True',
            "{{ organization_name }}",
            "{# comment #} True",
        ]:
            with self.subTest(condition=condition):
                self.assertIn("without", self._single_error(condition, self.sponsorship_events))

    def test_unknown_variable_suggests_the_closest_name(self) -> None:
        error = self._single_error('membership_type_categry == "sponsorship"', self.sponsorship_events)

        self.assertIn("'membership_type_categry'", error)
        self.assertIn("Did you mean 'membership_type_category'?", error)

    def test_variable_must_exist_for_every_selected_event(self) -> None:
        # The expired event has no "days", so the condition would quietly drop every expiry post.
        error = self._single_error("days <= 7", self.sponsorship_events)

        self.assertIn("organization_membership_expired", error)
        self.assertNotIn("organization_membership_expiring_soon", error)

    def test_checks_only_the_first_part_of_dotted_lookups_and_filter_arguments(self) -> None:
        events = ["organization_membership_expiring_soon"]

        self.assertEqual(
            condition_errors('membership.target_organization.name|default:organization_name == "Acme"', events), []
        )
        self.assertIn("'orgname'", self._single_error('organization_name|default:orgname == "Acme"', events))

    def test_common_variables_and_template_constants_are_allowed(self) -> None:
        self.assertEqual(condition_errors("actor == None or sponsor_notified == False", self.sponsorship_events), [])

    def test_rejects_unknown_membership_type_codes_in_either_operand_order(self) -> None:
        for condition in ['membership_type_code == "platnum"', '"platnum" != membership_type_code']:
            with self.subTest(condition=condition):
                error = self._single_error(condition, self.sponsorship_events)
                self.assertIn("'platnum' is not a valid membership_type_code", error)
                self.assertIn("platinum", error)

    def test_rejects_unknown_values_for_other_enumerated_variables(self) -> None:
        cases = [
            ('membership_type_category == "sponsorshp"', self.sponsorship_events),
            ('membership_target_kind == "org"', ["membership_request_submitted"]),
            ('termination_reason_category == "privcy"', ["membership_self_terminated"]),
            ('account_deletion_status == "done"', ["account_deletion_completed"]),
        ]
        for condition, events in cases:
            with self.subTest(condition=condition):
                self.assertIn("is not a valid", self._single_error(condition, events))

    def test_rejects_comparing_an_object_with_a_constant(self) -> None:
        # An object never equals a string or number, so the condition would silently never match.
        for condition, suggestion in [
            ('membership_type == "gold"', "membership_type_code"),
            ("organization != 5", "organization_id"),
        ]:
            with self.subTest(condition=condition):
                self.assertIn(suggestion, self._single_error(condition, self.sponsorship_events))

    def test_condition_keeps_spaces_inside_string_constants(self) -> None:
        template = _condition_template('organization_name == "Acme  Corp"\nand True')

        self.assertEqual(template.render(Context({"organization_name": "Acme  Corp"})), "1")

    def test_rejects_in_for_enumerated_variables(self) -> None:
        # Templates have no list literals: "in" against a string is a substring match.
        error = self._single_error('membership_type_code in "gold platinum"', self.sponsorship_events)

        self.assertIn("==", error)


@override_settings(MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=3)
class MattermostWebhookPostTests(SimpleTestCase):
    def _endpoint(self, *, url: str = "https://hooks.example.invalid/abc") -> MattermostWebhookEndpoint:
        return MattermostWebhookEndpoint(label="Ops", url=url, events=["election_opened"], enabled=True)

    def test_post_to_endpoint_success_2xx(self) -> None:
        response = Mock()
        response.status_code = 200
        response.text = "ok"

        with patch("core.mattermost_webhooks.requests.post", return_value=response) as post_mock:
            _post_to_endpoint(self._endpoint(), {"text": "hello"})

        post_mock.assert_called_once()
        _, kwargs = post_mock.call_args
        self.assertEqual(kwargs["timeout"], 3)
        self.assertFalse(kwargs["allow_redirects"])

    def test_post_to_endpoint_http_error_logged_and_swallowed(self) -> None:
        response = Mock()
        response.status_code = 500
        response.text = "boom"

        with (
            patch("core.mattermost_webhooks.requests.post", return_value=response),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(self._endpoint(), {"text": "hello"})

        self.assertTrue(captured.records)
        self.assertTrue(any("response_excerpt=boom" in record.getMessage() for record in captured.records))
        self.assertTrue(any(getattr(record, "response_excerpt", None) == "boom" for record in captured.records))

    def test_post_to_endpoint_exception_logged_and_swallowed(self) -> None:
        with (
            patch("core.mattermost_webhooks.requests.post", side_effect=requests.Timeout("timeout")),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(self._endpoint(), {"text": "hello"})

        self.assertTrue(captured.records)

    def test_post_to_endpoint_blocks_non_https(self) -> None:
        endpoint = self._endpoint(url="http://hooks.example.invalid/insecure")

        with (
            patch("core.mattermost_webhooks.requests.post") as post_mock,
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(endpoint, {"text": "hello"})

        post_mock.assert_not_called()
        self.assertTrue(captured.records)

    def test_post_to_endpoint_treats_redirect_as_failure(self) -> None:
        response = Mock()
        response.status_code = 302
        response.text = "redirect"

        with (
            patch("core.mattermost_webhooks.requests.post", return_value=response),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(self._endpoint(), {"text": "hello"})

        self.assertTrue(captured.records)

    @override_settings(
        MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=3,
        MATTERMOST_WEBHOOK_DEFAULT_USERNAME="AlmaLinux Astra",
        MATTERMOST_WEBHOOK_DEFAULT_ICON_URL="/core/static/core/images/almalinux_astra_small.png",
        PUBLIC_BASE_URL="https://accounts.example.org",
    )
    def test_post_to_endpoint_applies_default_identity_from_settings(self) -> None:
        response = Mock()
        response.status_code = 200
        response.text = "ok"

        with patch("core.mattermost_webhooks.requests.post", return_value=response) as post_mock:
            _post_to_endpoint(self._endpoint(), {"text": "hello"})

        _, kwargs = post_mock.call_args
        payload = kwargs["json"]
        self.assertEqual(payload["username"], "AlmaLinux Astra")
        self.assertEqual(
            payload["icon_url"],
            "https://accounts.example.org/core/static/core/images/almalinux_astra_small.png",
        )

    @override_settings(
        MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=3,
        MATTERMOST_WEBHOOK_DEFAULT_USERNAME="AlmaLinux Astra",
        MATTERMOST_WEBHOOK_DEFAULT_ICON_URL="/core/static/core/images/almalinux_astra_small.png",
    )
    def test_post_to_endpoint_keeps_explicit_payload_identity(self) -> None:
        response = Mock()
        response.status_code = 200
        response.text = "ok"

        with patch("core.mattermost_webhooks.requests.post", return_value=response) as post_mock:
            _post_to_endpoint(
                self._endpoint(),
                {
                    "text": "hello",
                    "username": "Custom Username",
                    "icon_url": "https://cdn.example.org/custom-icon.png",
                },
            )

        _, kwargs = post_mock.call_args
        payload = kwargs["json"]
        self.assertEqual(payload["username"], "Custom Username")
        self.assertEqual(payload["icon_url"], "https://cdn.example.org/custom-icon.png")

    def test_post_to_endpoint_never_logs_raw_url(self) -> None:
        endpoint = self._endpoint(url="https://hooks.example.invalid/secret-token-value")

        with (
            patch("core.mattermost_webhooks.requests.post", side_effect=requests.Timeout("timeout")),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(endpoint, {"text": "hello"})

        output = "\n".join(captured.output)
        self.assertNotIn(endpoint.url, output)

    def test_url_not_in_log_record_extra_on_transport_error(self) -> None:
        secret_url = "https://hooks.mattermost.example/secret-token-12345"
        endpoint = self._endpoint(url=secret_url)

        with (
            patch(
                "core.mattermost_webhooks.requests.post",
                side_effect=requests.exceptions.ConnectionError(f"Failed to connect to {secret_url}"),
            ),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            _post_to_endpoint(endpoint, {"text": "test"})

        for record in captured.records:
            for val in record.__dict__.values():
                self.assertNotIn(secret_url, str(val), f"URL leaked in log record field: {val!r}")


class MattermostWebhookDispatchTests(TestCase):
    def _dispatch_thread_patch(self):
        def _make_thread(*thread_ctor_args: object, target=None, args=(), kwargs=None, **_thread_kwargs: object):
            del thread_ctor_args
            call_kwargs = dict(kwargs or {})
            call_args = tuple(args or ())
            return SimpleNamespace(start=lambda: target(*call_args, **call_kwargs) if target is not None else None)

        return patch("core.mattermost_webhooks.threading.Thread", side_effect=_make_thread)

    def test_dispatch_skips_membership_request_event_when_membership_request_missing(self) -> None:
        MattermostWebhookEndpoint.objects.create(
            label="Membership",
            url="https://hooks.example.invalid/membership",
            enabled=True,
            events=["membership_request_submitted"],
        )

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self.assertLogs("core.mattermost_webhooks", level="WARNING") as captured,
        ):
            dispatch_mattermost_event("membership_request_submitted", actor="alice")

        post_mock.assert_not_called()
        self.assertTrue(any(record.getMessage() == "mattermost.membership_request_missing_context" for record in captured.records))

    def test_dispatch_skips_org_membership_event_when_membership_request_missing(self) -> None:
        MattermostWebhookEndpoint.objects.create(
            label="Org membership",
            url="https://hooks.example.invalid/org-membership",
            enabled=True,
            events=["organization_membership_request_submitted"],
        )

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self.assertLogs("core.mattermost_webhooks", level="WARNING") as captured,
        ):
            dispatch_mattermost_event(
                "organization_membership_request_submitted",
                actor="alice",
                organization_id=123,
            )

        post_mock.assert_not_called()
        self.assertTrue(any(record.getMessage() == "mattermost.membership_request_missing_context" for record in captured.records))

    def test_dispatch_only_posts_for_enabled_matching_event(self) -> None:
        enabled_match = MattermostWebhookEndpoint.objects.create(
            label="Enabled match",
            url="https://hooks.example.invalid/match",
            enabled=True,
            events=["election_opened"],
        )
        MattermostWebhookEndpoint.objects.create(
            label="Disabled",
            url="https://hooks.example.invalid/disabled",
            enabled=False,
            events=["election_opened"],
        )
        MattermostWebhookEndpoint.objects.create(
            label="Wrong event",
            url="https://hooks.example.invalid/other",
            enabled=True,
            events=["membership_expired"],
        )

        with patch("core.mattermost_webhooks._post_to_endpoint") as post_mock:
            dispatch_mattermost_event("election_opened", actor="alice")

        post_mock.assert_called_once()
        endpoint_arg = post_mock.call_args.args[0]
        self.assertEqual(endpoint_arg.pk, enabled_match.pk)

    def test_dispatch_accepts_signal_kwargs_that_include_event_key(self) -> None:
        # The debug signal sender adds event_key to the signal kwargs the receiver forwards.
        MattermostWebhookEndpoint.objects.create(
            label="Enabled match",
            url="https://hooks.example.invalid/match",
            enabled=True,
            events=["election_opened"],
        )

        with patch("core.mattermost_webhooks._post_to_endpoint") as post_mock:
            dispatch_mattermost_event("election_opened", event_key="election_opened", actor="alice")

        post_mock.assert_called_once()

    def test_dispatch_logs_error_and_skips_when_events_field_is_malformed(self) -> None:
        MattermostWebhookEndpoint.objects.create(
            label="Malformed",
            url="https://hooks.example.invalid/malformed",
            enabled=True,
            events={"bad": "shape"},
        )

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            dispatch_mattermost_event("election_opened", actor="alice")

        post_mock.assert_not_called()
        self.assertTrue(captured.records)

    def test_dispatch_skips_membership_count_events_when_count_is_zero(self) -> None:
        MattermostWebhookEndpoint.objects.create(
            label="Membership",
            url="https://hooks.example.invalid/membership",
            enabled=True,
            events=["membership_expired"],
        )

        with patch("core.mattermost_webhooks._post_to_endpoint") as post_mock:
            dispatch_mattermost_event("membership_expired", count=0, actor="system")

        post_mock.assert_not_called()

    def test_dispatch_skips_non_membership_event_when_count_is_zero(self) -> None:
        MattermostWebhookEndpoint.objects.create(
            label="Elections",
            url="https://hooks.example.invalid/elections-zero",
            enabled=True,
            events=["election_opened"],
        )

        with patch("core.mattermost_webhooks._post_to_endpoint") as post_mock:
            dispatch_mattermost_event("election_opened", count=0, actor="system")

        post_mock.assert_not_called()

    def test_dispatch_skips_membership_expiring_soon_when_count_is_zero(self) -> None:
        MattermostWebhookEndpoint.objects.create(
            label="Membership",
            url="https://hooks.example.invalid/membership-expiring",
            enabled=True,
            events=["membership_expiring_soon"],
        )

        with patch("core.mattermost_webhooks._post_to_endpoint") as post_mock:
            dispatch_mattermost_event("membership_expiring_soon", count=0, actor="system")

        post_mock.assert_not_called()

    def test_dispatch_continues_when_count_is_malformed(self) -> None:
        endpoint = MattermostWebhookEndpoint.objects.create(
            label="Elections",
            url="https://hooks.example.invalid/elections-malformed",
            enabled=True,
            events=["election_opened"],
        )

        with patch("core.mattermost_webhooks._post_to_endpoint") as post_mock:
            dispatch_mattermost_event("election_opened", count="two", actor="system")

        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.args[0].pk, endpoint.pk)

    def test_dispatch_logs_warning_and_skips_unknown_event_keys_in_endpoint_events(self) -> None:
        MattermostWebhookEndpoint.objects.create(
            label="Unknown route key",
            url="https://hooks.example.invalid/unknown",
            enabled=True,
            events=["event_from_future_registry"],
        )

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self.assertLogs("core.mattermost_webhooks", level="WARNING") as captured,
        ):
            dispatch_mattermost_event("election_opened", actor="alice")

        post_mock.assert_not_called()
        self.assertTrue(captured.records)

    @patch("core.mattermost_webhooks.MattermostWebhookEndpoint.objects.filter", side_effect=RuntimeError("DB down"))
    def test_dispatch_never_raises_on_db_error(self, _mock_filter: Mock) -> None:
        dispatch_mattermost_event("election_opened", actor="test")

    def _sponsorship(self, code: str) -> object:
        return SimpleNamespace(
            target_organization=SimpleNamespace(pk=5, name="Acme Corp", representative="jdoe"),
            membership_type=SimpleNamespace(code=code, name=code.title(), category_id="sponsorship", annual_dues_usd=0),
            dues_usd=None,
            expires_at=timezone.now(),
        )

    def _conditional_endpoint(self, label: str, condition: str) -> MattermostWebhookEndpoint:
        return MattermostWebhookEndpoint.objects.create(
            label=label,
            url=f"https://hooks.example.invalid/{label}",
            enabled=True,
            events=["organization_membership_expiring_soon"],
            condition=condition,
        )

    def _posted_labels(self, post_mock: Mock) -> list[str]:
        return sorted(call.args[0].label for call in post_mock.call_args_list)

    def test_dispatch_posts_only_to_endpoints_whose_condition_matches(self) -> None:
        self._conditional_endpoint("platinum", 'membership_type_code == "platinum"')
        self._conditional_endpoint("gold", 'membership_type_code == "gold"')
        self._conditional_endpoint("everything", "")

        with patch("core.mattermost_webhooks._post_to_endpoint") as post_mock:
            dispatch_mattermost_event(
                "organization_membership_expiring_soon", membership=self._sponsorship("gold"), days=7, sponsor_notified=True
            )

        self.assertEqual(self._posted_labels(post_mock), ["everything", "gold"])

    def test_dispatch_evaluates_a_condition_typed_over_several_lines(self) -> None:
        # Template tags cannot span lines; unjoined, this would render as literal text and always match.
        self._conditional_endpoint("platinum-soon", 'membership_type_code == "platinum"\nor days <= 7')

        with patch("core.mattermost_webhooks._post_to_endpoint") as post_mock:
            dispatch_mattermost_event(
                "organization_membership_expiring_soon", membership=self._sponsorship("gold"), days=30, sponsor_notified=True
            )

        post_mock.assert_not_called()

    def test_dispatch_skips_and_logs_an_endpoint_whose_condition_fails(self) -> None:
        broken = self._conditional_endpoint("broken", "days <")
        self._conditional_endpoint("everything", "")

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            dispatch_mattermost_event(
                "organization_membership_expiring_soon", membership=self._sponsorship("gold"), days=7, sponsor_notified=True
            )

        self.assertEqual(self._posted_labels(post_mock), ["everything"])
        self.assertTrue(
            any(
                record.getMessage() == "mattermost.dispatch_error" and record.endpoint_id == broken.pk
                for record in captured.records
            )
        )

    def test_account_invitation_accepted_signal_posts_through_connected_receiver(self) -> None:
        endpoint = MattermostWebhookEndpoint.objects.create(
            label="Invites",
            url="https://hooks.example.invalid/invites",
            enabled=True,
            events=["account_invitation_accepted"],
        )
        invitation = AccountInvitation.objects.create(
            email="invitee@example.com",
            full_name="Invitee",
            invited_by_username="committee",
            accepted_username="alice",
        )

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self._dispatch_thread_patch(),
        ):
            CANONICAL_SIGNALS["account_invitation_accepted"].send(
                sender=AccountInvitation,
                account_invitation=invitation,
                actor="alice",
            )

        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.args[0].pk, endpoint.pk)
        payload = post_mock.call_args.args[1]
        self.assertEqual(payload["text"], "Account invitation accepted")

    def test_organization_claimed_signal_posts_through_connected_receiver(self) -> None:
        endpoint = MattermostWebhookEndpoint.objects.create(
            label="Organizations",
            url="https://hooks.example.invalid/orgs",
            enabled=True,
            events=["organization_claimed"],
        )
        organization = Organization.objects.create(name="Claimed Org")

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self._dispatch_thread_patch(),
        ):
            CANONICAL_SIGNALS["organization_claimed"].send(
                sender=Organization,
                organization=organization,
                actor="alice",
            )

        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.args[0].pk, endpoint.pk)
        payload = post_mock.call_args.args[1]
        self.assertEqual(payload["text"], "Organization claimed")

    def test_membership_self_terminated_signal_posts_through_connected_receiver(self) -> None:
        endpoint = MattermostWebhookEndpoint.objects.create(
            label="Membership",
            url="https://hooks.example.invalid/membership",
            enabled=True,
            events=["membership_self_terminated"],
        )
        membership_type = SimpleNamespace(code="individual", name="Individual", category_id="individual")
        feedback = SimpleNamespace(pk=7, reason_category="no_longer_needed", reason_text="Private details")

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self._dispatch_thread_patch(),
        ):
            CANONICAL_SIGNALS["membership_self_terminated"].send(
                sender=MembershipLog,
                actor="alice",
                username="alice",
                membership_type=membership_type,
                termination_feedback=feedback,
            )

        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.args[0].pk, endpoint.pk)
        payload = post_mock.call_args.args[1]
        self.assertEqual(payload["text"], "Membership self-terminated")
        attachment = payload["attachments"][0]
        self.assertTrue(
            attachment["title_link"].endswith(reverse("admin:core_membershipterminationfeedback_change", args=[7]))
        )
        self.assertIn({"title": "Reason", "value": "No longer needed", "short": True}, attachment["fields"])
        # The free text is wiped after the privacy window; Mattermost would keep it forever.
        self.assertNotIn("Private details", json.dumps(payload))

    def test_membership_self_terminated_template_exposes_the_reason_category(self) -> None:
        MattermostWebhookEndpoint.objects.create(
            label="Membership",
            url="https://hooks.example.invalid/membership",
            enabled=True,
            events=["membership_self_terminated"],
            text="{{ username }} left ({{ termination_reason_category }}) #{{ termination_feedback_id }}",
        )

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self._dispatch_thread_patch(),
        ):
            CANONICAL_SIGNALS["membership_self_terminated"].send(
                sender=MembershipLog,
                actor="alice",
                username="alice",
                membership_type=SimpleNamespace(code="individual", name="Individual", category_id="individual"),
                termination_feedback=SimpleNamespace(pk=7, reason_category="privacy", reason_text=""),
            )

        self.assertEqual(post_mock.call_args.args[1]["text"], "alice left (privacy) #7")

    def test_signals_from_scheduled_jobs_post_before_the_run_returns(self) -> None:
        # A cron process exits after its last job; a daemon dispatch thread would die with it.
        MattermostWebhookEndpoint.objects.create(
            label="Organizations",
            url="https://hooks.example.invalid/orgs",
            enabled=True,
            events=["organization_created"],
        )
        organization = Organization.objects.create(name="Scheduled Org")

        def _job(_command_name: str, **_kwargs: object) -> None:
            CANONICAL_SIGNALS["organization_created"].send(sender=Organization, organization=organization)

        with (
            patch("core.operations.call_command", side_effect=_job),
            patch("core.mattermost_webhooks.threading.Thread") as thread_mock,
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
        ):
            run_operations(name="operations_test", jobs=[("some_job", {})])
            # Outside a scheduled run, requests still dispatch in the background.
            CANONICAL_SIGNALS["organization_created"].send(sender=Organization, organization=organization)

        post_mock.assert_called_once()
        thread_mock.assert_called_once()

    def test_account_deletion_completed_signal_posts_through_connected_receiver(self) -> None:
        endpoint = MattermostWebhookEndpoint.objects.create(
            label="Privacy",
            url="https://hooks.example.invalid/privacy",
            enabled=True,
            events=["account_deletion_completed"],
        )
        deletion_request = SimpleNamespace(
            pk=42,
            username="alice",
            status="completed",
            manual_review_required=False,
            blocker_codes=[],
        )

        with (
            patch("core.mattermost_webhooks._post_to_endpoint") as post_mock,
            self._dispatch_thread_patch(),
        ):
            CANONICAL_SIGNALS["account_deletion_completed"].send(
                sender=type(deletion_request),
                account_deletion_request=deletion_request,
                actor="admin",
            )

        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.args[0].pk, endpoint.pk)
        payload = post_mock.call_args.args[1]
        self.assertEqual(payload["text"], "Account deletion completed")
        self.assertTrue(
            payload["attachments"][0]["title_link"].endswith(
                reverse("admin:core_accountdeletionrequest_change", args=[42])
            )
        )


class MattermostWebhookAdminTests(TestCase):
    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _admin_user(self, username: str = "alice") -> FreeIPAUser:
        return FreeIPAUser(username, {"uid": [username], "memberof_group": ["admins"]})

    def _endpoint(self) -> MattermostWebhookEndpoint:
        return MattermostWebhookEndpoint.objects.create(
            label="Ops endpoint",
            url="https://hooks.example.invalid/test",
            enabled=True,
            events=["election_opened"],
        )

    def test_change_page_renders_send_test_notification_button(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send test notification")
        test_url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
        self.assertContains(response, f'formaction="{test_url}"')

    def test_change_page_renders_events_as_checkbox_multiselect(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_events_0"')
        self.assertNotContains(response, '<textarea name="events"')

    def test_change_page_renders_help_text_hints_for_message_fields(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Django template syntax")
        self.assertContains(response, "JSON list of Mattermost attachments")
        self.assertContains(response, "JSON object merged into Mattermost props")

    def test_change_page_shows_template_variables_of_selected_events_below_the_tabs(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Outside the tabs, so it stays readable while editing the condition or message content.
        self.assertNotIn('href="#template-variables-reference-tab"', content)
        self.assertGreater(content.index('id="template-variables-reference"'), content.index('id="priority-optional-tab"'))
        self.assertContains(response, "election.name")
        self.assertContains(response, "election_genesis_hash")
        self.assertContains(response, "Election Opened")
        # Every event is rendered so the script can reveal it when ticked, without saving first.
        self.assertContains(response, 'data-template-variables-event="election_opened">')
        self.assertContains(response, 'data-template-variables-event="membership_expired" hidden>')
        self.assertContains(response, 'data-template-variables-empty hidden>')
        self.assertContains(response, "core/js/mattermost_webhook_template_variables.js")

    def test_change_page_lists_each_events_variables_alphabetically(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        content = response.content.decode()
        section_start = content.index('data-template-variables-event="election_opened"')
        section = content[section_start : content.index("</tbody>", section_start)]
        names = re.findall(r'<td class="template-variables-name"><code>([^<]+)</code>', section)
        self.assertIn("election_vote_url", names)
        self.assertEqual(names, sorted(names))

    def test_add_page_shows_no_event_variables_until_events_are_selected(self) -> None:
        self._login_as_freeipa_admin()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-template-variables-event="election_opened" hidden>')
        self.assertContains(response, "data-template-variables-empty>")

    def test_change_page_puts_the_condition_in_its_own_tab_after_routing(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        tab_order = [content.index(f'href="#{slug}-tab"') for slug in ("routing", "condition", "mattermost-overrides")]
        self.assertEqual(tab_order, sorted(tab_order))
        condition_pane = content.index('id="condition-tab"')
        self.assertGreater(content.index('name="condition"'), condition_pane)
        self.assertLess(content.index('name="events"'), condition_pane)

    def test_change_page_renders_priority_controls_instead_of_raw_priority_json(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_priority_level"')
        self.assertContains(response, 'id="id_priority_requested_ack"')
        self.assertContains(response, 'id="id_priority_persistent_notifications"')
        self.assertNotContains(response, '<textarea name="priority"')

    def test_change_view_saves_priority_controls_to_priority_json(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            change_url = reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk])
            result = self.client.post(
                change_url,
                data={
                    "label": endpoint.label,
                    "url": endpoint.url,
                    "enabled": "on",
                    "events": ["election_opened"],
                    "channel": "",
                    "username": "",
                    "icon_url": "",
                    "text": "",
                    "attachments": "",
                    "props": "",
                    "priority_level": "important",
                    "priority_requested_ack": "on",
                    "_save": "Save",
                },
                follow=True,
            )

        self.assertEqual(result.status_code, 200)
        endpoint.refresh_from_db()
        self.assertEqual(endpoint.priority, {"priority": "important", "requested_ack": True})

    def test_change_view_rejects_invalid_priority_control_combination(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            change_url = reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk])
            result = self.client.post(
                change_url,
                data={
                    "label": endpoint.label,
                    "url": endpoint.url,
                    "enabled": "on",
                    "events": ["election_opened"],
                    "channel": "",
                    "username": "",
                    "icon_url": "",
                    "text": "",
                    "attachments": "",
                    "props": "",
                    "priority_level": "standard",
                    "priority_requested_ack": "on",
                    "priority_persistent_notifications": "on",
                    "_save": "Save",
                },
                follow=True,
            )

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Requested acknowledgement is only available for Important or Urgent.")
        self.assertContains(result, "Persistent notifications are only available for Urgent.")
        endpoint.refresh_from_db()
        self.assertIsNone(endpoint.priority)

    def _post_condition(self, endpoint: MattermostWebhookEndpoint, condition: str):
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            return self.client.post(
                reverse("admin:core_mattermostwebhookendpoint_change", args=[endpoint.pk]),
                data={
                    "label": endpoint.label,
                    "url": endpoint.url,
                    "enabled": "on",
                    "events": ["election_opened"],
                    "condition": condition,
                    "_save": "Save",
                },
                follow=True,
            )

    def test_change_view_saves_a_valid_condition(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        result = self._post_condition(endpoint, 'election_name == "Board"')

        self.assertEqual(result.status_code, 200)
        endpoint.refresh_from_db()
        self.assertEqual(endpoint.condition, 'election_name == "Board"')

    def test_change_view_rejects_a_condition_using_an_unknown_variable(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        result = self._post_condition(endpoint, 'election_nam == "Board"')

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Did you mean &#x27;election_name&#x27;?")
        endpoint.refresh_from_db()
        self.assertEqual(endpoint.condition, "")

    def test_changelist_flags_a_condition_that_went_stale_after_saving(self) -> None:
        self._login_as_freeipa_admin()
        MattermostWebhookEndpoint.objects.create(
            label="Retired tier",
            url="https://hooks.example.invalid/retired",
            enabled=True,
            events=["organization_membership_expired"],
            condition='membership_type_code == "retired"',
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            response = self.client.get(reverse("admin:core_mattermostwebhookendpoint_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "&#x27;retired&#x27; is not a valid membership_type_code")

    @override_settings(MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=5)
    def test_test_notification_view_success(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()
        response = Mock()
        response.status_code = 200
        response.text = "ok"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()),
            patch("core.mattermost_webhooks.requests.post", return_value=response),
            self.assertLogs("core.mattermost_webhooks", level="INFO") as captured,
        ):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.post(url, data={"post": "1"}, follow=True)

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Test notification sent successfully")
        self.assertTrue(any(record.getMessage().startswith("mattermost.admin_test_start") for record in captured.records))
        success_records = [record for record in captured.records if record.getMessage().startswith("mattermost.admin_test_success")]
        self.assertTrue(success_records)
        success_message = success_records[0].getMessage()
        self.assertIn("ref=", success_message)
        self.assertIn(f"endpoint_id={endpoint.pk}", success_message)
        self.assertIn("duration_ms=", success_message)

        for record in captured.records:
            for value in record.__dict__.values():
                self.assertNotIn(endpoint.url, str(value))

    @override_settings(MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=5)
    def test_test_notification_view_http_failure(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()
        response = Mock()
        response.status_code = 500
        response.text = f"server failed while posting to {endpoint.url}"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()),
            patch("core.mattermost_webhooks.requests.post", return_value=response),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.post(url, data={"post": "1"}, follow=True)

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Test failed: HTTP 500")
        self.assertContains(result, "Response:")
        self.assertContains(result, "server failed while posting to [redacted-url]")
        self.assertContains(result, "Ref:")
        message_texts = [str(message) for message in result.context["messages"]]
        self.assertTrue(any("Test failed: HTTP 500" in message for message in message_texts))
        self.assertFalse(any(endpoint.url in message for message in message_texts))
        self.assertTrue(any(record.getMessage().startswith("mattermost.admin_test_failed") for record in captured.records))
        self.assertTrue(any(getattr(record, "endpoint_id", None) == endpoint.pk for record in captured.records))
        self.assertTrue(any(getattr(record, "status_code", None) == 500 for record in captured.records))
        self.assertTrue(any("ref=" in record.getMessage() for record in captured.records))
        self.assertTrue(any("response_excerpt=server failed while posting to [redacted-url]" in record.getMessage() for record in captured.records))
        self.assertTrue(any("payload_json=" in record.getMessage() for record in captured.records))
        self.assertTrue(any("duration_ms=" in record.getMessage() for record in captured.records))

        failed_records = [record for record in captured.records if record.getMessage().startswith("mattermost.admin_test_failed")]
        self.assertTrue(failed_records)
        payload_json = getattr(failed_records[0], "payload_json", None)
        self.assertIsNotNone(payload_json)
        parsed_payload = json.loads(str(payload_json))
        self.assertIn("text", parsed_payload)
        self.assertTrue(str(parsed_payload["text"]).startswith("[TEST] "))

        for record in captured.records:
            for value in record.__dict__.values():
                self.assertNotIn(endpoint.url, str(value))

    @override_settings(MATTERMOST_WEBHOOK_TIMEOUT_SECONDS=5)
    def test_test_notification_view_exception_failure_does_not_show_url(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()),
            patch(
                "core.mattermost_webhooks.requests.post",
                side_effect=requests.exceptions.ConnectionError(f"Failed to connect to {endpoint.url}"),
            ),
            self.assertLogs("core.mattermost_webhooks", level="ERROR") as captured,
        ):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.post(url, data={"post": "1"}, follow=True)

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Test failed:")
        self.assertContains(result, "Ref:")
        message_texts = [str(message) for message in result.context["messages"]]
        self.assertTrue(any("Test failed:" in message for message in message_texts))
        self.assertFalse(any(endpoint.url in message for message in message_texts))
        self.assertTrue(any(record.getMessage().startswith("mattermost.admin_test_failed") for record in captured.records))
        self.assertTrue(any(getattr(record, "error", None) == "connection_error" for record in captured.records))
        self.assertTrue(any("ref=" in record.getMessage() for record in captured.records))
        self.assertTrue(any("duration_ms=" in record.getMessage() for record in captured.records))

        for record in captured.records:
            for value in record.__dict__.values():
                self.assertNotIn(endpoint.url, str(value))

    def test_test_notification_view_get_redirects_with_warning(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.get(url, follow=True)

        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Test notifications must be sent with POST")

    def test_test_notification_view_payload_is_labeled_as_test(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = self._endpoint()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()),
            patch(
                "core.mattermost_webhooks.post_mattermost_payload",
                return_value=(True, 200, "", None, None),
            ) as post_mock,
        ):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.post(url, data={"post": "1"}, follow=True)

        self.assertEqual(result.status_code, 200)
        payload = post_mock.call_args.args[1]
        self.assertIn("text", payload)

        text = str(payload["text"])
        self.assertTrue(text.startswith("[TEST] "))
        self.assertIn(str(endpoint.label), text)

        timestamp = text.rsplit(" - ", 1)[1]
        parsed = datetime.datetime.fromisoformat(timestamp)
        self.assertIsNotNone(parsed.tzinfo)

    def test_test_notification_view_payload_applies_endpoint_overrides(self) -> None:
        self._login_as_freeipa_admin()
        endpoint = MattermostWebhookEndpoint.objects.create(
            label="Ops endpoint",
            url="https://hooks.example.invalid/test",
            enabled=True,
            events=["election_opened"],
            text="[TEST configured] {{ actor }} ref={{ test_ref }}",
            attachments=[
                {
                    "title": "{{ endpoint_label }}",
                    "text": "test ref {{ test_ref }}",
                }
            ],
            props={"source": "admin-test"},
            priority={"priority": "urgent", "requested_ack": True, "persistent_notifications": True},
            channel="ops-alerts",
            username="Ops Bot",
            icon_url="https://example.invalid/icon.png",
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._admin_user()),
            patch(
                "core.mattermost_webhooks.post_mattermost_payload",
                return_value=(True, 200, "", None, None),
            ) as post_mock,
        ):
            url = reverse("admin:core_mattermostwebhookendpoint_test", args=[endpoint.pk])
            result = self.client.post(url, data={"post": "1"}, follow=True)

        self.assertEqual(result.status_code, 200)
        payload = post_mock.call_args.args[1]
        self.assertTrue(str(payload["text"]).startswith("[TEST configured]"))
        self.assertEqual(payload["priority"], {"priority": "urgent", "requested_ack": True, "persistent_notifications": True})
        self.assertEqual(payload["channel"], "ops-alerts")
        self.assertEqual(payload["username"], "Ops Bot")
        self.assertEqual(payload["icon_url"], "https://example.invalid/icon.png")
        self.assertEqual(payload["props"], {"source": "admin-test"})
        self.assertEqual(payload["attachments"][0]["title"], "Ops endpoint")
