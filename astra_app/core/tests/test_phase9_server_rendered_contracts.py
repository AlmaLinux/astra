import datetime
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from post_office.models import EmailTemplate

from core.freeipa.user import FreeIPAUser
from core.models import AccountInvitation, Election, FreeIPAPermissionGrant, MembershipRequest, MembershipType
from core.permissions import ASTRA_ADD_ELECTION, ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL
from core.tests.utils_test_data import ensure_core_categories


class Phase9ServerRenderedContractsTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _reviewer(self) -> FreeIPAUser:
        return FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
                "memberofindirect_group": [],
            },
        )

    def test_bulk_table_contracts_render_for_membership_requests_and_account_invitations(self) -> None:
        self._login_as_freeipa_user("reviewer")

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
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )
        MembershipRequest.objects.create(
            requested_username="bob",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now() - datetime.timedelta(days=1),
        )

        AccountInvitation.objects.create(
            email="pending@example.com",
            full_name="Pending User",
            note="",
            invited_by_username="reviewer",
        )
        AccountInvitation.objects.create(
            email="accepted@example.com",
            full_name="Accepted User",
            note="",
            invited_by_username="reviewer",
            accepted_at=timezone.now(),
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            requests_resp = self.client.get(reverse("membership-requests"))
            invitations_resp = self.client.get(reverse("account-invitations"))

        self.assertEqual(requests_resp.status_code, 200)
        self.assertEqual(invitations_resp.status_code, 200)

        requests_html = requests_resp.content.decode("utf-8")
        invitations_html = invitations_resp.content.decode("utf-8")

        self.assertIn('data-bulk-table-form', requests_html)
        self.assertIn('data-bulk-select-all-id="select-all-requests"', requests_html)
        self.assertIn('data-bulk-apply-id="bulk-apply"', requests_html)
        self.assertIn('data-bulk-checkbox-selector=".request-checkbox--pending"', requests_html)
        self.assertIn('id="select-all-requests"', requests_html)
        self.assertIn('id="bulk-apply"', requests_html)
        self.assertIn('select name="bulk_action"', requests_html)
        self.assertIn('class="request-checkbox request-checkbox--pending"', requests_html)
        self.assertIn('form="bulk-action-form"', requests_html)

        self.assertIn('data-bulk-table-form', invitations_html)
        self.assertIn('data-bulk-select-all-id="select-all-invitations-pending"', invitations_html)
        self.assertIn('data-bulk-apply-id="bulk-apply-pending"', invitations_html)
        self.assertIn('data-bulk-checkbox-selector=".invitation-checkbox--pending"', invitations_html)
        self.assertIn('id="select-all-invitations-pending"', invitations_html)
        self.assertIn('id="bulk-apply-pending"', invitations_html)
        self.assertIn('select name="bulk_action"', invitations_html)
        self.assertIn('class="invitation-checkbox invitation-checkbox--pending"', invitations_html)
        self.assertIn('form="bulk-invitations-pending-form"', invitations_html)

    def test_membership_shared_modal_contract_is_rendered_with_trigger_data_attributes(self) -> None:
        self._login_as_freeipa_user("reviewer")

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
        MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            resp = self.client.get(reverse("membership-requests"))

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        self.assertIn('id="shared-approve-modal"', html)
        self.assertIn('id="shared-reject-modal"', html)
        self.assertIn('id="shared-rfi-modal"', html)
        self.assertIn('id="shared-ignore-modal"', html)

        self.assertIn('class="js-body-prefix"', html)
        self.assertIn('class="js-body-emphasis"', html)
        self.assertIn('class="js-body-suffix"', html)

        self.assertIn('data-target="#shared-approve-modal"', html)
        self.assertIn('data-target="#shared-reject-modal"', html)
        self.assertIn('data-target="#shared-rfi-modal"', html)
        self.assertIn('data-target="#shared-ignore-modal"', html)
        self.assertIn('data-action-url="', html)
        self.assertIn('data-modal-title="', html)
        self.assertIn('data-body-prefix="', html)
        self.assertIn('data-body-emphasis="', html)
        self.assertIn('data-body-suffix="', html)
        self.assertIn('data-request-id="', html)
        self.assertIn('data-request-target="', html)
        self.assertIn('data-membership-type="', html)

        self.assertIn('Request #<strong class="js-request-id"></strong>', html)
        self.assertIn('Requested for <strong class="js-request-target"></strong>', html)
        self.assertIn('Membership type <strong class="js-membership-type"></strong>', html)

        self.assertIn('src="/static/core/js/membership_request_shared_modals.js"', html)

    def test_compose_embed_contract_renders_for_send_mail_election_edit_and_email_template_edit(self) -> None:
        self._login_as_freeipa_user("reviewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Compose contract election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.draft,
        )
        template = EmailTemplate.objects.create(
            name="phase9-compose-contract",
            subject="Subject",
            content="Text",
            html_content="<p>HTML</p>",
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            send_mail_resp = self.client.get(reverse("send-mail"))
            election_edit_resp = self.client.get(reverse("election-edit", args=[election.pk]))
            template_edit_resp = self.client.get(reverse("email-template-edit", kwargs={"template_id": template.pk}))

        self.assertEqual(send_mail_resp.status_code, 200)
        self.assertEqual(election_edit_resp.status_code, 200)
        self.assertEqual(template_edit_resp.status_code, 200)

        for html in (
            send_mail_resp.content.decode("utf-8"),
            election_edit_resp.content.decode("utf-8"),
            template_edit_resp.content.decode("utf-8"),
        ):
            self.assertIn("data-templated-email-compose", html)
            self.assertIn("data-compose-action", html)
            self.assertIn("data-compose-preview", html)
            self.assertIn("data-compose-preview-iframe", html)
            self.assertIn("core/vendor/codemirror/codemirror.min.css", html)
            self.assertIn("core/js/templated_email_compose_init.js", html)
