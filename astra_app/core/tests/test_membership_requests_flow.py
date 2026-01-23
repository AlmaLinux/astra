from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
)


class MembershipRequestsFlowTests(TestCase):
    def setUp(self) -> None:
        super().setUp()

        for perm in (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=perm,
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_user_can_request_membership_and_email_is_sent(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "acceptance_template": None,
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
                # Membership requests are gated by having a valid country.
                "fasstatusnote": ["US"],
                "memberof_group": [],
            },
        )
        self._login_as_freeipa_user("alice")

        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            with patch("post_office.mail.send", autospec=True) as send_mock:
                resp = self.client.post(
                    reverse("membership-request"),
                    data={
                        "membership_type": "individual",
                        "q_contributions": "I contributed docs and CI improvements.",
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        req = MembershipRequest.objects.get(requested_username="alice", membership_type_id="individual")
        self.assertEqual(req.status, MembershipRequest.Status.pending)
        self.assertEqual(req.responses, [{"Contributions": "I contributed docs and CI improvements."}])
        self.assertTrue(MembershipLog.objects.filter(
            target_username="alice",
            membership_type_id="individual",
            action=MembershipLog.Action.requested,
        ).exists())

        send_mock.assert_called_once()
        _, kwargs = send_mock.call_args
        self.assertEqual(kwargs["recipients"], ["alice@example.com"])
        self.assertEqual(kwargs["sender"], settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(kwargs["template"], settings.MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME)
        self.assertEqual(kwargs["context"]["username"], "alice")
        self.assertIn("first_name", kwargs["context"])
        self.assertIn("last_name", kwargs["context"])
        self.assertIn("full_name", kwargs["context"])
        self.assertNotIn("displayname", kwargs["context"])
        self.assertEqual(kwargs["context"]["membership_type"], "Individual")

    def test_membership_request_form_hides_membership_types_with_pending_request(self) -> None:
        from core.models import MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 1,
                "enabled": True,
            },
        )

        MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
                "fasstatusnote": ["US"],
                "memberof_group": [],
            },
        )
        self._login_as_freeipa_user("alice")

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.forms_membership.get_valid_membership_type_codes_for_username", return_value=set()),
            patch("core.forms_membership.get_extendable_membership_type_codes_for_username", return_value=set()),
        ):
            resp = self.client.get(reverse("membership-request"))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'value="individual"')
        self.assertContains(resp, 'value="mirror"')

    def test_committee_can_approve_request_adds_user_to_group_logs_and_emails(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock:
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    resp = self.client.post(
                        reverse("membership-request-approve", args=[req.pk]),
                        follow=False,
                    )

        self.assertEqual(resp.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.approved)
        add_mock.assert_called_once()
        _, call_kwargs = add_mock.call_args
        self.assertEqual(call_kwargs["group_name"], "almalinux-individual")

        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.approved,
            ).exists()
        )

        send_mock.assert_called_once()
        _, kwargs = send_mock.call_args
        self.assertEqual(kwargs["recipients"], ["alice@example.com"])
        self.assertEqual(kwargs["template"], "membership-request-approved-individual")

    def test_committee_approve_is_blocked_if_configured_template_is_missing(self) -> None:
        import uuid

        from post_office.models import EmailTemplate

        from core.models import MembershipLog, MembershipRequest, MembershipType

        missing_name = f"missing-approval-template-{uuid.uuid4()}"
        EmailTemplate.objects.filter(name=missing_name).delete()
        self.assertFalse(EmailTemplate.objects.filter(name=missing_name).exists())

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "acceptance_template": None,
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_type = MembershipType.objects.get(code="individual")
        self.assertIsNone(membership_type.acceptance_template_id)
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with override_settings(MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME=missing_name):
            self.assertEqual(settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME, missing_name)
            with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
                with patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock:
                    resp = self.client.post(reverse("membership-request-approve", args=[req.pk]), follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Configured email template")

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.pending)
        add_mock.assert_not_called()
        self.assertFalse(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.approved,
            ).exists()
        )

    def test_committee_can_approve_request_with_custom_email_redirects_to_send_mail(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "add_to_group", autospec=True):
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    resp = self.client.post(
                        reverse("membership-request-approve", args=[req.pk]),
                        data={"custom_email": "1"},
                        follow=False,
                    )

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_not_called()

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.approved)
        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.approved,
            ).exists()
        )

        redirect_url = str(resp["Location"])
        self.assertTrue(redirect_url.startswith(reverse("send-mail") + "?"))
        qs = parse_qs(urlsplit(redirect_url).query)
        self.assertEqual(qs.get("type"), ["users"])
        self.assertEqual(qs.get("to"), ["alice"])
        self.assertEqual(qs.get("template"), [settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME])


    def test_committee_can_approve_org_request_sends_email_using_membership_type_template(self) -> None:
        from post_office.models import EmailTemplate

        from core.models import MembershipRequest, MembershipType, Organization

        template, _ = EmailTemplate.objects.update_or_create(
            name="membership-request-approved-silver",
            defaults={
                "subject": "Approved",
                "content": "Approved",
                "html_content": "<p>Approved</p>",
                "description": "Org approval template",
            },
        )

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "almalinux-sponsor-silver",
                "acceptance_template": template,
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="CERN",
            business_contact_email="cern@example.com",
            representative="bob",
        )
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock:
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    resp = self.client.post(
                        reverse("membership-request-approve", args=[req.pk]),
                        follow=False,
                    )

        self.assertEqual(resp.status_code, 302)
        add_mock.assert_called_once()
        _, add_kwargs = add_mock.call_args
        self.assertEqual(add_kwargs["group_name"], "almalinux-sponsor-silver")

        send_mock.assert_called_once()
        _, kwargs = send_mock.call_args
        self.assertEqual(kwargs["recipients"], ["bob@example.com"])
        self.assertEqual(kwargs["template"], "membership-request-approved-silver")

    def test_committee_can_approve_org_request_with_custom_email_redirects_to_send_mail(self) -> None:
        from post_office.models import EmailTemplate

        from core.models import MembershipRequest, MembershipType, Organization

        template, _ = EmailTemplate.objects.update_or_create(
            name="membership-request-approved-silver",
            defaults={
                "subject": "Approved",
                "content": "Approved",
                "html_content": "<p>Approved</p>",
                "description": "Org approval template",
            },
        )

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "almalinux-sponsor-silver",
                "acceptance_template": template,
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="CERN",
            business_contact_email="cern@example.com",
            representative="bob",
        )
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "add_to_group", autospec=True):
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    resp = self.client.post(
                        reverse("membership-request-approve", args=[req.pk]),
                        data={"custom_email": "1"},
                        follow=False,
                    )

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_not_called()

        redirect_url = str(resp["Location"])
        self.assertTrue(redirect_url.startswith(reverse("send-mail") + "?"))
        qs = parse_qs(urlsplit(redirect_url).query)
        self.assertEqual(qs.get("type"), ["users"])
        self.assertEqual(qs.get("to"), ["bob"])
        self.assertEqual(qs.get("template"), ["membership-request-approved-silver"])


    def test_committee_can_approve_org_request_email_includes_org_contacts_and_representative_context(self) -> None:
        from core.models import MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "almalinux-sponsor-silver",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="CERN",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            representative="bob",
        )
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "givenname": ["Bob"],
                "sn": ["User"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "add_to_group", autospec=True):
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    resp = self.client.post(
                        reverse("membership-request-approve", args=[req.pk]),
                        follow=False,
                    )

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_called_once()
        _, kwargs = send_mock.call_args
        ctx = kwargs["context"]
        self.assertEqual(kwargs["recipients"], ["bob@example.com"])

        # Organization contact variables must always be present.
        self.assertEqual(ctx["business_contact_name"], "Biz")
        self.assertEqual(ctx["business_contact_email"], "biz@example.com")
        self.assertEqual(ctx["pr_marketing_contact_name"], "PR")
        self.assertEqual(ctx["pr_marketing_contact_email"], "pr@example.com")
        self.assertEqual(ctx["technical_contact_name"], "Tech")
        self.assertEqual(ctx["technical_contact_email"], "tech@example.com")

        # Representative context should be available via canonical user variables.
        self.assertEqual(ctx["username"], "bob")
        self.assertEqual(ctx["email"], "bob@example.com")
        self.assertEqual(ctx["first_name"], "Bob")
        self.assertEqual(ctx["last_name"], "User")
        self.assertEqual(ctx["full_name"], "Bob User")

    def test_uninterrupted_extension_preserves_membership_created_at(self) -> None:
        import datetime

        from core.models import Membership, MembershipLog, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_type = MembershipType.objects.get(code="individual")

        start_at = datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        extend_at = datetime.datetime(2025, 2, 1, 12, 0, 0, tzinfo=datetime.UTC)

        with patch("django.utils.timezone.now", autospec=True, return_value=start_at):
            first_log = MembershipLog.create_for_approval(
                actor_username="reviewer",
                target_username="alice",
                membership_type=membership_type,
                previous_expires_at=None,
                membership_request=None,
            )

        membership = Membership.objects.get(target_username="alice", membership_type=membership_type)
        self.assertEqual(membership.created_at, start_at)

        # Simulate a missing current-state row (e.g. sync drift) while the membership
        # is still considered uninterrupted via logs.
        previous_expires_at = first_log.expires_at
        assert previous_expires_at is not None
        Membership.objects.filter(target_username="alice", membership_type=membership_type).delete()

        with patch("django.utils.timezone.now", autospec=True, return_value=extend_at):
            MembershipLog.create_for_approval(
                actor_username="reviewer",
                target_username="alice",
                membership_type=membership_type,
                previous_expires_at=previous_expires_at,
                membership_request=None,
            )

        recreated = Membership.objects.get(target_username="alice", membership_type=membership_type)
        self.assertEqual(recreated.created_at, start_at)
        self.assertGreater(recreated.expires_at, previous_expires_at)

    def test_expired_membership_starts_new_term_and_resets_created_at(self) -> None:
        import datetime

        from core.models import Membership, MembershipLog, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        membership_type = MembershipType.objects.get(code="individual")

        start_at = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        after_expiry_at = datetime.datetime(2025, 7, 1, 12, 0, 0, tzinfo=datetime.UTC)

        with patch("django.utils.timezone.now", autospec=True, return_value=start_at):
            MembershipLog.create_for_approval(
                actor_username="reviewer",
                target_username="alice",
                membership_type=membership_type,
                previous_expires_at=None,
                membership_request=None,
            )

        # Force an expired current-state row (simulating a lingering row from an old term).
        Membership.objects.filter(target_username="alice", membership_type=membership_type).update(
            expires_at=start_at,
        )

        with patch("django.utils.timezone.now", autospec=True, return_value=after_expiry_at):
            MembershipLog.create_for_approval(
                actor_username="reviewer",
                target_username="alice",
                membership_type=membership_type,
                previous_expires_at=start_at,
                membership_request=None,
            )

        current = Membership.objects.get(target_username="alice", membership_type=membership_type)
        self.assertEqual(current.created_at, after_expiry_at)

    def test_committee_can_reject_request_logs_and_emails_with_reason(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("post_office.mail.send", autospec=True) as send_mock:
                resp = self.client.post(
                    reverse("membership-request-reject", args=[req.pk]),
                    data={"reason": "Missing required info"},
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.rejected)

        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.rejected,
                rejection_reason__icontains="Missing required info",
            ).exists()
        )

        send_mock.assert_called_once()
        _, kwargs = send_mock.call_args
        self.assertEqual(kwargs["recipients"], ["alice@example.com"])
        self.assertEqual(kwargs["template"], settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME)
        self.assertIn("first_name", kwargs["context"])
        self.assertIn("last_name", kwargs["context"])
        self.assertIn("full_name", kwargs["context"])
        self.assertNotIn("displayname", kwargs["context"])
        self.assertIn("Missing required info", kwargs["context"]["rejection_reason"])

    def test_committee_can_reject_org_request_with_custom_email_redirects_to_send_mail(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="CERN", business_contact_email="cern@example.com", representative="bob")
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        self._login_as_freeipa_user("reviewer")

        reason = "Missing paperwork"

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("post_office.mail.send", autospec=True) as send_mock:
                resp = self.client.post(
                    reverse("membership-request-reject", args=[req.pk]),
                    data={"reason": reason, "custom_email": "1"},
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_not_called()

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.rejected)
        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_organization=org,
                membership_type_id="silver",
                action=MembershipLog.Action.rejected,
            ).exists()
        )

        redirect_url = str(resp["Location"])
        self.assertTrue(redirect_url.startswith(reverse("send-mail") + "?"))
        qs = parse_qs(urlsplit(redirect_url).query)
        self.assertEqual(qs.get("type"), ["users"])
        self.assertEqual(qs.get("to"), ["bob"])
        self.assertEqual(qs.get("template"), [settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME])
        self.assertEqual(qs.get("rejection_reason"), [reason])

    def test_bulk_actions_send_org_reject_email(self) -> None:
        from core.models import MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="CERN", business_contact_email="cern@example.com", representative="bob")
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )
        self._login_as_freeipa_user("reviewer")

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("post_office.mail.send", autospec=True) as send_mock:
                resp = self.client.post(
                    reverse("membership-requests-bulk"),
                    data={"bulk_action": "reject", "selected": [str(req.pk)]},
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_called_once()
        _, kwargs = send_mock.call_args
        self.assertEqual(kwargs["recipients"], ["bob@example.com"])
        self.assertEqual(kwargs["template"], settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME)

    def test_bulk_actions_send_org_approve_email(self) -> None:
        from post_office.models import EmailTemplate

        from core.models import MembershipRequest, MembershipType, Organization

        template, _ = EmailTemplate.objects.update_or_create(
            name="membership-request-approved-silver",
            defaults={
                "subject": "Approved",
                "content": "Approved",
                "html_content": "<p>Approved</p>",
                "description": "Org approval template",
            },
        )

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "almalinux-sponsor-silver",
                "acceptance_template": template,
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="CERN",
            business_contact_email="cern@example.com",
            representative="bob",
        )
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock:
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    resp = self.client.post(
                        reverse("membership-requests-bulk"),
                        data={"bulk_action": "approve", "selected": [str(req.pk)]},
                        follow=False,
                    )

        self.assertEqual(resp.status_code, 302)
        add_mock.assert_called_once()
        _, add_kwargs = add_mock.call_args
        self.assertEqual(add_kwargs["group_name"], "almalinux-sponsor-silver")

        send_mock.assert_called_once()
        _, kwargs = send_mock.call_args
        self.assertEqual(kwargs["recipients"], ["bob@example.com"])
        self.assertEqual(kwargs["template"], "membership-request-approved-silver")

    def test_committee_can_reject_request_with_custom_email_redirects_to_send_mail(self) -> None:
        from urllib.parse import parse_qs, urlparse

        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        reason = "Missing required info"
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("post_office.mail.send", autospec=True) as send_mock:
                resp = self.client.post(
                    reverse("membership-request-reject", args=[req.pk]),
                    data={"reason": reason, "custom_email": "1"},
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_not_called()

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.rejected)
        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.rejected,
                rejection_reason__icontains=reason,
            ).exists()
        )

        location = resp["Location"]
        parsed = urlparse(location)
        self.assertEqual(parsed.path, reverse("send-mail"))
        qs = parse_qs(parsed.query)
        self.assertEqual(qs["type"], ["users"])
        self.assertEqual(qs["to"], ["alice"])
        self.assertEqual(qs["template"], [settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME])
        self.assertEqual(qs["rejection_reason"], [reason])

    def test_committee_can_reject_org_request_email_includes_org_contacts_and_representative_context(self) -> None:
        from core.models import MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="CERN",
            business_contact_name="Biz",
            business_contact_email="biz@example.com",
            pr_marketing_contact_name="PR",
            pr_marketing_contact_email="pr@example.com",
            technical_contact_name="Tech",
            technical_contact_email="tech@example.com",
            representative="bob",
        )
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "givenname": ["Bob"],
                "sn": ["User"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("post_office.mail.send", autospec=True) as send_mock:
                resp = self.client.post(
                    reverse("membership-request-reject", args=[req.pk]),
                    data={"reason": "Nope"},
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_called_once()
        _, kwargs = send_mock.call_args
        ctx = kwargs["context"]
        self.assertEqual(kwargs["recipients"], ["bob@example.com"])

        self.assertEqual(ctx["business_contact_name"], "Biz")
        self.assertEqual(ctx["business_contact_email"], "biz@example.com")
        self.assertEqual(ctx["pr_marketing_contact_name"], "PR")
        self.assertEqual(ctx["pr_marketing_contact_email"], "pr@example.com")
        self.assertEqual(ctx["technical_contact_name"], "Tech")
        self.assertEqual(ctx["technical_contact_email"], "tech@example.com")

        self.assertEqual(ctx["username"], "bob")
        self.assertEqual(ctx["email"], "bob@example.com")
        self.assertEqual(ctx["first_name"], "Bob")
        self.assertEqual(ctx["last_name"], "User")
        self.assertEqual(ctx["full_name"], "Bob User")

    def test_reject_requires_post(self) -> None:
        from core.models import MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.get(reverse("membership-request-reject", args=[req.pk]))

        self.assertEqual(resp.status_code, 404)

    def test_committee_can_approve_org_request_with_representative_redirects_to_send_mail_users(self) -> None:
        from post_office.models import EmailTemplate

        from core.models import MembershipRequest, MembershipType, Organization

        template, _ = EmailTemplate.objects.update_or_create(
            name="membership-request-approved-silver",
            defaults={
                "subject": "Approved",
                "content": "Approved",
                "html_content": "<p>Approved</p>",
                "description": "Org approval template",
            },
        )

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "almalinux-sponsor-silver",
                "acceptance_template": template,
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="CERN",
            business_contact_email="cern@example.com",
            representative="bob",
        )
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "givenname": ["Bob"],
                "sn": ["User"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "add_to_group", autospec=True):
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    resp = self.client.post(
                        reverse("membership-request-approve", args=[req.pk]),
                        data={"custom_email": "1"},
                        follow=False,
                    )

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_not_called()

        redirect_url = str(resp["Location"])
        self.assertTrue(redirect_url.startswith(reverse("send-mail") + "?"))
        qs = parse_qs(urlsplit(redirect_url).query)
        self.assertEqual(qs.get("type"), ["users"])
        self.assertEqual(qs.get("to"), ["bob"])
        self.assertEqual(qs.get("template"), ["membership-request-approved-silver"])

    def test_committee_can_reject_org_request_with_representative_redirects_to_send_mail_users(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="silver",
            defaults={
                "name": "Silver Sponsor",
                "group_cn": "",
                "isIndividual": False,
                "isOrganization": True,
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(
            name="CERN",
            business_contact_email="cern@example.com",
            representative="bob",
        )
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="silver",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "givenname": ["Bob"],
                "sn": ["User"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")

        reason = "Missing paperwork"
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("post_office.mail.send", autospec=True) as send_mock:
                resp = self.client.post(
                    reverse("membership-request-reject", args=[req.pk]),
                    data={"reason": reason, "custom_email": "1"},
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        send_mock.assert_not_called()

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.rejected)
        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_organization=org,
                membership_type_id="silver",
                action=MembershipLog.Action.rejected,
            ).exists()
        )

        redirect_url = str(resp["Location"])
        self.assertTrue(redirect_url.startswith(reverse("send-mail") + "?"))
        qs = parse_qs(urlsplit(redirect_url).query)
        self.assertEqual(qs.get("type"), ["users"])
        self.assertEqual(qs.get("to"), ["bob"])
        self.assertEqual(qs.get("template"), [settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME])
        self.assertEqual(qs.get("rejection_reason"), [reason])

    def test_committee_can_ignore_request_logs_and_does_not_email(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            with patch("post_office.mail.send", autospec=True) as send_mock:
                resp = self.client.post(
                    reverse("membership-request-ignore", args=[req.pk]),
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.ignored)
        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.ignored,
            ).exists()
        )
        send_mock.assert_not_called()

    def test_extension_starts_when_current_membership_ends(self) -> None:
        import datetime

        from django.utils import timezone

        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )

        now = timezone.now()
        current_expires = now + datetime.timedelta(days=100)
        MembershipLog.objects.create(
            actor_username="reviewer",
            target_username="alice",
            membership_type_id="individual",
            requested_group_cn="almalinux-individual",
            action=MembershipLog.Action.approved,
            expires_at=current_expires,
        )

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        self._login_as_freeipa_user("reviewer")

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "add_to_group", autospec=True):
                with patch("post_office.mail.send", autospec=True):
                    resp = self.client.post(reverse("membership-request-approve", args=[req.pk]), follow=False)

        self.assertEqual(resp.status_code, 302)
        latest = (
            MembershipLog.objects.filter(
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.approved,
            )
            .order_by("-created_at")
            .first()
        )
        self.assertIsNotNone(latest)
        assert latest is not None
        expected = current_expires + datetime.timedelta(days=settings.MEMBERSHIP_VALIDITY_DAYS)
        self.assertEqual(latest.expires_at, expected)

    def test_pending_request_count_renders_in_nav_for_committee(self) -> None:
        from core.models import MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        MembershipRequest.objects.create(requested_username="bob", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        self._login_as_freeipa_user("reviewer")
        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            with patch("core.views_users.FreeIPAUser.all", autospec=True, return_value=[]):
                resp = self.client.get(reverse("users"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'navbar-badge">2<')
        self.assertContains(resp, 'href="/membership/requests/"')
        self.assertContains(resp, 'Pending requests')

    def test_approval_expiry_is_end_of_day_utc(self) -> None:
        import datetime

        from core.models import MembershipLog, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )
        mt = MembershipType.objects.get(code="individual")

        approved_at = datetime.datetime(2026, 1, 20, 10, 15, 0, tzinfo=datetime.UTC)
        with patch("django.utils.timezone.now", autospec=True, return_value=approved_at):
            log = MembershipLog.create_for_approval(
                actor_username="reviewer",
                target_username="alice",
                membership_type=mt,
            )

        expected_expires_at = datetime.datetime(
            2027,
            1,
            20,
            23,
            59,
            59,
            tzinfo=datetime.UTC,
        )
        self.assertEqual(log.expires_at, expected_expires_at)

    def test_committee_can_bulk_approve_requests(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )

        req1 = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        req2 = MembershipRequest.objects.create(requested_username="bob", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_mock:
                with patch("post_office.mail.send", autospec=True) as send_mock:
                    resp = self.client.post(
                        reverse("membership-requests-bulk"),
                        data={
                            "bulk_action": "approve",
                            "selected": [str(req1.pk), str(req2.pk)],
                        },
                        follow=False,
                    )

        self.assertEqual(resp.status_code, 302)
        req1.refresh_from_db()
        req2.refresh_from_db()
        self.assertEqual(req1.status, MembershipRequest.Status.approved)
        self.assertEqual(req2.status, MembershipRequest.Status.approved)
        self.assertEqual(add_mock.call_count, 2)
        send_mock.assert_called()
        self.assertEqual(send_mock.call_count, 2)

        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="alice",
                membership_type_id="individual",
                action=MembershipLog.Action.approved,
            ).exists()
        )
        self.assertTrue(
            MembershipLog.objects.filter(
                actor_username="reviewer",
                target_username="bob",
                membership_type_id="individual",
                action=MembershipLog.Action.approved,
            ).exists()
        )

    def test_committee_can_bulk_ignore_requests(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )

        req1 = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        req2 = MembershipRequest.objects.create(requested_username="bob", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )

        self._login_as_freeipa_user("reviewer")
        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            with patch("post_office.mail.send", autospec=True) as send_mock:
                resp = self.client.post(
                    reverse("membership-requests-bulk"),
                    data={
                        "bulk_action": "ignore",
                        "selected": [str(req1.pk), str(req2.pk)],
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        req1.refresh_from_db()
        req2.refresh_from_db()
        self.assertEqual(req1.status, MembershipRequest.Status.ignored)
        self.assertEqual(req2.status, MembershipRequest.Status.ignored)
        send_mock.assert_not_called()
        self.assertTrue(MembershipLog.objects.filter(target_username="alice", action=MembershipLog.Action.ignored).exists())
        self.assertTrue(MembershipLog.objects.filter(target_username="bob", action=MembershipLog.Action.ignored).exists())

    def test_committee_can_bulk_reject_requests(self) -> None:
        from core.models import MembershipLog, MembershipRequest, MembershipType

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "isIndividual": True,
                "isOrganization": False,
                "sort_order": 0,
                "enabled": True,
            },
        )

        req1 = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        req2 = MembershipRequest.objects.create(requested_username="bob", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [committee_cn],
            },
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str) -> FreeIPAUser | None:
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            return None

        self._login_as_freeipa_user("reviewer")
        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            with patch("post_office.mail.send", autospec=True) as send_mock:
                resp = self.client.post(
                    reverse("membership-requests-bulk"),
                    data={
                        "bulk_action": "reject",
                        "selected": [str(req1.pk), str(req2.pk)],
                    },
                    follow=False,
                )

        self.assertEqual(resp.status_code, 302)
        req1.refresh_from_db()
        req2.refresh_from_db()
        self.assertEqual(req1.status, MembershipRequest.Status.rejected)
        self.assertEqual(req2.status, MembershipRequest.Status.rejected)
        self.assertEqual(send_mock.call_count, 2)
        self.assertTrue(MembershipLog.objects.filter(target_username="alice", action=MembershipLog.Action.rejected).exists())
        self.assertTrue(MembershipLog.objects.filter(target_username="bob", action=MembershipLog.Action.rejected).exists())
