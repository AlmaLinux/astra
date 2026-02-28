import datetime
from typing import override
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.models import AuditLogEntry, Election, FreeIPAPermissionGrant, Organization
from core.permissions import ASTRA_CHANGE_MEMBERSHIP
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates


class OrganizationAuditLogTests(TestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        ensure_email_templates()

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _valid_org_payload(self, organization: Organization) -> dict[str, str]:
        return {
            "name": organization.name,
            "country_code": organization.country_code,
            "business_contact_name": organization.business_contact_name,
            "business_contact_email": organization.business_contact_email,
            "business_contact_phone": organization.business_contact_phone,
            "pr_marketing_contact_name": organization.pr_marketing_contact_name,
            "pr_marketing_contact_email": organization.pr_marketing_contact_email,
            "pr_marketing_contact_phone": organization.pr_marketing_contact_phone,
            "technical_contact_name": organization.technical_contact_name,
            "technical_contact_email": organization.technical_contact_email,
            "technical_contact_phone": organization.technical_contact_phone,
            "website_logo": organization.website_logo,
            "website": organization.website,
            "street": organization.street,
            "city": organization.city,
            "state": organization.state,
            "postal_code": organization.postal_code,
        }

    def _create_org(self, *, representative: str) -> Organization:
        return Organization.objects.create(
            name="Test Org",
            representative=representative,
            country_code="US",
            business_contact_name="Business",
            business_contact_email="business@example.com",
            pr_marketing_contact_name="Marketing",
            pr_marketing_contact_email="marketing@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            website_logo="https://example.com/logo-options",
            website="https://example.com/",
            street="1 Main St",
            city="Austin",
            state="TX",
            postal_code="73301",
        )

    def test_edit_with_changed_fields_creates_organization_edited_audit_entry(self) -> None:
        organization = self._create_org(representative="alice")
        self._login_as_freeipa_user("alice")

        payload = self._valid_org_payload(organization)
        payload["technical_contact_email"] = "new-tech@example.com"

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            response = self.client.post(reverse("organization-edit", args=[organization.pk]), data=payload, follow=False)

        self.assertEqual(response.status_code, 302)

        entry = AuditLogEntry.objects.get(organization=organization, event_type="organization_edited")
        self.assertEqual(entry.payload.get("actor_username"), "alice")
        self.assertEqual(entry.payload.get("changed_fields"), ["technical_contact_email"])
        self.assertFalse(entry.is_public)

    def test_representative_change_creates_representative_changed_audit_entry(self) -> None:
        organization = self._create_org(representative="carol")
        self._login_as_freeipa_user("reviewer")

        FreeIPAPermissionGrant.objects.create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

        payload = self._valid_org_payload(organization)
        payload["representative"] = "bob"

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [], "c": ["US"]})
        bob = FreeIPAUser("bob", {"uid": ["bob"], "memberof_group": [], "c": ["US"]})
        carol = FreeIPAUser("carol", {"uid": ["carol"], "memberof_group": [], "c": ["US"]})

        def _freeipa_get(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            if username == "carol":
                return carol
            return None

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=_freeipa_get):
            response = self.client.post(reverse("organization-edit", args=[organization.pk]), data=payload, follow=False)

        self.assertEqual(response.status_code, 302)

        entry = AuditLogEntry.objects.get(
            organization=organization,
            event_type="organization_representative_changed",
        )
        self.assertEqual(
            entry.payload,
            {
                "old": "carol",
                "new": "bob",
                "actor": "reviewer",
            },
        )
        self.assertFalse(entry.is_public)

    def test_unchanged_edit_does_not_create_organization_edited_audit_entry(self) -> None:
        organization = self._create_org(representative="alice")
        self._login_as_freeipa_user("alice")

        payload = self._valid_org_payload(organization)
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": [], "c": ["US"]})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            response = self.client.post(reverse("organization-edit", args=[organization.pk]), data=payload, follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            AuditLogEntry.objects.filter(
                organization=organization,
                event_type="organization_edited",
            ).exists()
        )

    def test_election_audit_entry_still_supports_election_association(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Audit test election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        entry = AuditLogEntry.objects.create(
            election=election,
            event_type="election_tallied",
            payload={"ok": True},
            is_public=True,
        )

        self.assertEqual(entry.election_id, election.pk)
        self.assertIsNone(entry.organization_id)
