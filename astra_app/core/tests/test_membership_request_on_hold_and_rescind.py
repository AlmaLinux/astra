
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from post_office.models import Email

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipRequest
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_ADD_SEND_MAIL,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
)


class MembershipRequestOnHoldAndRescindTests(TestCase):
    def setUp(self) -> None:
        super().setUp()

        self._freeipa_users: dict[str, FreeIPAUser] = {}
        patcher = patch("core.backends.FreeIPAUser.get", side_effect=self._get_freeipa_user)
        patcher.start()
        self.addCleanup(patcher.stop)

        for perm in (
            ASTRA_ADD_MEMBERSHIP,
            ASTRA_CHANGE_MEMBERSHIP,
            ASTRA_DELETE_MEMBERSHIP,
            ASTRA_VIEW_MEMBERSHIP,
            ASTRA_ADD_SEND_MAIL,
        ):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=perm,
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _get_freeipa_user(self, username: str) -> FreeIPAUser | None:
        return self._freeipa_users.get(str(username))

    def _add_freeipa_user(
        self,
        *,
        username: str,
        email: str | None = None,
        groups: list[str] | None = None,
        first_name: str = "",
        last_name: str = "",
    ) -> FreeIPAUser:
        user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": [first_name] if first_name else [],
                "sn": [last_name] if last_name else [],
                "mail": [email] if email else [],
                "memberof_group": list(groups or []),
            },
        )
        self._freeipa_users[username] = user
        return user

    def test_committee_can_rfi_request_sets_on_hold_sends_email_and_logs(self) -> None:
        from core.models import MembershipLog, MembershipType, Note

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
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        self._add_freeipa_user(
            username="reviewer",
            email="reviewer@example.com",
            groups=[committee_cn],
            first_name="Reviewer",
            last_name="User",
        )
        self._add_freeipa_user(
            username="alice",
            email="alice@example.com",
            groups=[],
            first_name="Alice",
            last_name="User",
        )

        self._login_as_freeipa_user("reviewer")

        email = Email.objects.create(
            from_email="noreply@example.com",
            to="alice@example.com",
            cc="",
            bcc="",
            subject="RFI",
            message="RFI text",
            html_message="",
        )

        with patch("post_office.mail.send", autospec=True, return_value=email) as send_mock:
            resp = self.client.post(
                reverse("membership-request-rfi", args=[req.pk]),
                data={"rfi_message": "Please clarify your contributions."},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.on_hold)
        self.assertIsNotNone(req.on_hold_at)

        self.assertTrue(
            MembershipLog.objects.filter(
                membership_request=req,
                actor_username="reviewer",
                action=MembershipLog.Action.on_hold,
            ).exists()
        )
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action__type="request_on_hold",
            ).exists()
        )
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action__type="request_on_hold",
                content__isnull=False,
            )
            .exclude(content="")
            .exists()
        )
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action__type="contacted",
                action__kind="rfi",
                action__email_id=email.id,
            ).exists()
        )

        send_mock.assert_called_once()
        _, kwargs = send_mock.call_args
        self.assertEqual(kwargs["recipients"], ["alice@example.com"])
        self.assertEqual(kwargs["template"], settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME)
        self.assertIn("rfi_message", kwargs["context"])
        self.assertIn("application_url", kwargs["context"])
        self.assertTrue(kwargs["context"]["application_url"].endswith(reverse("membership-request-self", args=[req.pk])))

    def test_committee_rfi_is_idempotent_for_on_hold(self) -> None:
        from core.models import MembershipLog, MembershipType

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

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now(),
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        self._add_freeipa_user(
            username="reviewer",
            email="reviewer@example.com",
            groups=[committee_cn],
            first_name="Reviewer",
            last_name="User",
        )
        self._add_freeipa_user(
            username="alice",
            email="alice@example.com",
            groups=[],
            first_name="Alice",
            last_name="User",
        )

        self._login_as_freeipa_user("reviewer")

        existing_logs = MembershipLog.objects.filter(
            membership_request=req,
            action=MembershipLog.Action.on_hold,
        ).count()

        with patch("post_office.mail.send", autospec=True) as send_mock:
            resp = self.client.post(
                reverse("membership-request-rfi", args=[req.pk]),
                data={"rfi_message": "Already on hold."},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.on_hold)

        self.assertEqual(
            MembershipLog.objects.filter(
                membership_request=req,
                action=MembershipLog.Action.on_hold,
            ).count(),
            existing_logs,
        )
        send_mock.assert_not_called()

    def test_user_cannot_view_other_users_request(self) -> None:
        from core.models import MembershipType

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
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._add_freeipa_user(username="alice", email="alice@example.com")
        self._add_freeipa_user(username="bob", email="bob@example.com")

        self._login_as_freeipa_user("bob")
        resp = self.client.get(reverse("membership-request-self", args=[req.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_user_can_view_pending_request_but_cannot_edit(self) -> None:
        from core.models import MembershipType

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
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
            responses=[{"Contributions": "Old"}],
        )

        self._add_freeipa_user(username="alice", email="alice@example.com")

        self._login_as_freeipa_user("alice")
        resp_get = self.client.get(reverse("membership-request-self", args=[req.pk]))
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, 'title="Cancel your membership request"')

        resp_post = self.client.post(
            reverse("membership-request-self", args=[req.pk]),
            data={"q_contributions": "New"},
            follow=False,
        )
        self.assertEqual(resp_post.status_code, 403)

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.pending)
        self.assertEqual(req.responses, [{"Contributions": "Old"}])

    def test_reject_preserves_original_request_responses(self) -> None:
        from core.models import MembershipType

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

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
            responses=[{"Contributions": "Old"}],
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        self._add_freeipa_user(
            username="reviewer",
            email="reviewer@example.com",
            groups=[committee_cn],
            first_name="Reviewer",
            last_name="User",
        )
        self._add_freeipa_user(
            username="alice",
            email="alice@example.com",
            groups=[],
            first_name="Alice",
            last_name="User",
        )

        self._login_as_freeipa_user("reviewer")
        with patch("post_office.mail.send", autospec=True):
            resp = self.client.post(
                reverse("membership-request-reject", args=[req.pk]),
                data={"reason": "No."},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.rejected)
        self.assertTrue(any(item.get("Contributions") == "Old" for item in req.responses))
        self.assertTrue(any(item.get("Rejection reason") == "No." for item in req.responses))

        resp_detail = self.client.get(reverse("membership-request-detail", args=[req.pk]))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, "Request responses")
        self.assertContains(resp_detail, "Contributions")

    def test_committee_approve_is_idempotent_for_approved(self) -> None:
        from core.models import MembershipType

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

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.approved,
            decided_at=timezone.now(),
            decided_by_username="reviewer",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        self._add_freeipa_user(
            username="reviewer",
            email="reviewer@example.com",
            groups=[committee_cn],
            first_name="Reviewer",
            last_name="User",
        )

        self._login_as_freeipa_user("reviewer")

        with patch("core.views_membership.approve_membership_request", autospec=True) as approve_mock:
            resp = self.client.post(
                reverse("membership-request-approve", args=[req.pk]),
                data={},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        approve_mock.assert_not_called()

    def test_committee_reject_is_idempotent_for_rejected(self) -> None:
        from core.models import MembershipType

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

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.rejected,
            decided_at=timezone.now(),
            decided_by_username="reviewer",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        self._add_freeipa_user(
            username="reviewer",
            email="reviewer@example.com",
            groups=[committee_cn],
            first_name="Reviewer",
            last_name="User",
        )

        self._login_as_freeipa_user("reviewer")

        with patch("core.views_membership.reject_membership_request", autospec=True) as reject_mock:
            resp = self.client.post(
                reverse("membership-request-reject", args=[req.pk]),
                data={"reason": "No."},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        reject_mock.assert_not_called()

    def test_committee_ignore_is_idempotent_for_ignored(self) -> None:
        from core.models import MembershipType

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

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.ignored,
            decided_at=timezone.now(),
            decided_by_username="reviewer",
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        self._add_freeipa_user(
            username="reviewer",
            email="reviewer@example.com",
            groups=[committee_cn],
            first_name="Reviewer",
            last_name="User",
        )

        self._login_as_freeipa_user("reviewer")

        with patch("core.views_membership.ignore_membership_request", autospec=True) as ignore_mock:
            resp = self.client.post(
                reverse("membership-request-ignore", args=[req.pk]),
                data={},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        ignore_mock.assert_not_called()

    def test_reject_preserves_original_org_request_responses(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="org",
            defaults={
                "name": "Organization",
                "group_cn": "almalinux-org",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="org",
            status=MembershipRequest.Status.pending,
            responses=[{"Additional Information": "Org answers"}],
        )

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        self._add_freeipa_user(
            username="reviewer",
            email="reviewer@example.com",
            groups=[committee_cn],
            first_name="Reviewer",
            last_name="User",
        )
        self._add_freeipa_user(
            username="bob",
            email="bob@example.com",
            groups=[],
            first_name="Bob",
            last_name="Rep",
        )

        self._login_as_freeipa_user("reviewer")
        with patch("post_office.mail.send", autospec=True):
            resp = self.client.post(
                reverse("membership-request-reject", args=[req.pk]),
                data={"reason": "No."},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.rejected)
        self.assertTrue(any(item.get("Additional Information") == "Org answers" for item in req.responses))
        self.assertTrue(any(item.get("Rejection reason") == "No." for item in req.responses))

        resp_detail = self.client.get(reverse("membership-request-detail", args=[req.pk]))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, "Request responses")
        self.assertContains(resp_detail, "Additional Information")
        self.assertContains(resp_detail, "Org answers")

    def test_org_rfi_resubmit_replaces_additional_information(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="org",
            defaults={
                "name": "Organization",
                "group_cn": "almalinux-org",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="org",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now(),
            responses=[{"Additional Information": "Old"}],
        )

        self._add_freeipa_user(username="bob", email="bob@example.com")

        self._login_as_freeipa_user("bob")
        resp_post = self.client.post(
            reverse("membership-request-self", args=[req.pk]),
            data={
                "q_additional_information": "New",
            },
            follow=False,
        )

        self.assertEqual(resp_post.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.pending)
        self.assertIsNone(req.on_hold_at)

        additional_info_items = [
            item
            for item in req.responses
            if isinstance(item, dict)
            and any(str(k).strip().lower() == "additional information" for k in item.keys())
        ]
        self.assertEqual(len(additional_info_items), 1)
        self.assertTrue(any(item.get("Additional information") == "New" for item in additional_info_items))

    def test_membership_request_self_readonly_shows_legacy_additional_information_key(self) -> None:
        from core.models import MembershipType, Organization

        MembershipType.objects.update_or_create(
            code="org",
            defaults={
                "name": "Organization",
                "group_cn": "almalinux-org",
                "category_id": "sponsorship",
                "sort_order": 0,
                "enabled": True,
            },
        )

        org = Organization.objects.create(name="Acme", representative="bob")
        req = MembershipRequest.objects.create(
            requested_username="",
            requested_organization=org,
            membership_type_id="org",
            status=MembershipRequest.Status.pending,
            responses=[{"Additional Information": "Legacy details"}],
        )

        self._add_freeipa_user(username="bob", email="bob@example.com")
        self._login_as_freeipa_user("bob")

        resp = self.client.get(reverse("membership-request-self", args=[req.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Additional information")
        self.assertContains(resp, "Legacy details")

    def test_user_can_edit_on_hold_request_and_submit_returns_to_pending(self) -> None:
        from core.models import MembershipLog, MembershipType, Note

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
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now(),
            responses=[{"Contributions": "Old"}],
        )

        self._add_freeipa_user(username="alice", email="alice@example.com")

        self._login_as_freeipa_user("alice")

        resp_post = self.client.post(
            reverse("membership-request-self", args=[req.pk]),
            data={
                "q_contributions": "Updated",
                "q_additional_information": "More details.",
            },
            follow=False,
        )

        self.assertEqual(resp_post.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.pending)
        self.assertIsNone(req.on_hold_at)
        self.assertTrue(
            MembershipLog.objects.filter(
                membership_request=req,
                actor_username="alice",
                action=MembershipLog.Action.resubmitted,
            ).exists()
        )
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="alice",
                action__type="request_resubmitted",
            ).exists()
        )

    def test_user_cannot_resubmit_on_hold_request_without_changes(self) -> None:
        from core.models import MembershipType

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
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now(),
            responses=[{"Contributions": "Same"}],
        )

        self._add_freeipa_user(username="alice", email="alice@example.com")

        self._login_as_freeipa_user("alice")
        resp_post = self.client.post(
            reverse("membership-request-self", args=[req.pk]),
            data={
                "q_contributions": "Same",
                "q_additional_information": "",
            },
            follow=False,
        )

        self.assertEqual(resp_post.status_code, 200)
        self.assertContains(resp_post, "Please update your request before resubmitting it")
        self.assertNotContains(resp_post, "['")

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.on_hold)
        self.assertIsNotNone(req.on_hold_at)
        self.assertEqual(req.responses, [{"Contributions": "Same"}])

    def test_on_hold_self_service_page_offers_rescind_action(self) -> None:
        from core.models import MembershipType

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

        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.on_hold,
            on_hold_at=timezone.now(),
            responses=[{"Contributions": "Old"}],
        )

        self._add_freeipa_user(username="alice", email="alice@example.com")
        self._login_as_freeipa_user("alice")

        resp = self.client.get(reverse("membership-request-self", args=[req.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Rescind request")
        self.assertContains(resp, 'title="Cancel your membership request"')
        self.assertContains(resp, f'action="{reverse("membership-request-rescind", args=[req.pk])}"')

    def test_rfi_custom_email_redirects_to_send_mail_with_recipient_and_template(self) -> None:
        from core.models import MembershipType

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

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        self._add_freeipa_user(
            username="reviewer",
            email="reviewer@example.com",
            groups=[committee_cn],
            first_name="Reviewer",
            last_name="User",
        )
        self._add_freeipa_user(
            username="alice",
            email="alice@example.com",
            groups=[],
            first_name="Alice",
            last_name="User",
        )

        self._login_as_freeipa_user("reviewer")
        resp = self.client.post(
            reverse("membership-request-rfi", args=[req.pk]),
            data={"rfi_message": "Please clarify.", "custom_email": "1"},
            follow=False,
        )

        self.assertEqual(resp.status_code, 302)
        target = resp["Location"]
        parsed = urlparse(target)
        self.assertEqual(parsed.path, reverse("send-mail"))
        qs = parse_qs(parsed.query)
        self.assertEqual(qs.get("type"), ["users"])
        self.assertEqual(qs.get("to"), ["alice"])
        self.assertEqual(qs.get("template"), [settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME])

    def test_rfi_custom_email_redirects_even_without_rfi_message(self) -> None:
        from core.models import MembershipType

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

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        self._add_freeipa_user(
            username="reviewer",
            email="reviewer@example.com",
            groups=[committee_cn],
            first_name="Reviewer",
            last_name="User",
        )
        self._add_freeipa_user(
            username="alice",
            email="alice@example.com",
            groups=[],
            first_name="Alice",
            last_name="User",
        )

        self._login_as_freeipa_user("reviewer")
        resp = self.client.post(
            reverse("membership-request-rfi", args=[req.pk]),
            data={"rfi_message": "", "custom_email": "1"},
            follow=False,
        )

        self.assertEqual(resp.status_code, 302)
        target = resp["Location"]
        parsed = urlparse(target)
        self.assertEqual(parsed.path, reverse("send-mail"))
        qs = parse_qs(parsed.query)
        self.assertEqual(qs.get("type"), ["users"])
        self.assertEqual(qs.get("to"), ["alice"])
        self.assertEqual(qs.get("template"), [settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME])

    def test_rfi_without_message_still_sends_email_and_puts_on_hold(self) -> None:
        from core.models import MembershipLog, MembershipType, Note

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

        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        committee_cn = settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP
        self._add_freeipa_user(
            username="reviewer",
            email="reviewer@example.com",
            groups=[committee_cn],
            first_name="Reviewer",
            last_name="User",
        )
        self._add_freeipa_user(
            username="alice",
            email="alice@example.com",
            groups=[],
            first_name="Alice",
            last_name="User",
        )

        self._login_as_freeipa_user("reviewer")

        with patch("post_office.mail.send", autospec=True) as send_mock:
            resp = self.client.post(
                reverse("membership-request-rfi", args=[req.pk]),
                data={"rfi_message": ""},
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.on_hold)
        self.assertIsNotNone(req.on_hold_at)

        self.assertTrue(
            MembershipLog.objects.filter(
                membership_request=req,
                actor_username="reviewer",
                action=MembershipLog.Action.on_hold,
            ).exists()
        )
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action__type="request_on_hold",
            ).exists()
        )

        send_mock.assert_called_once()
        _, kwargs = send_mock.call_args
        self.assertEqual(kwargs["recipients"], ["alice@example.com"])
        self.assertEqual(kwargs["template"], settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME)
        self.assertIn("rfi_message", kwargs["context"])
        self.assertEqual(kwargs["context"]["rfi_message"], "")

    def test_user_can_rescind_pending_request(self) -> None:
        from core.models import MembershipLog, MembershipType, Note

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
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
        )

        self._add_freeipa_user(username="alice", email="alice@example.com")

        self._login_as_freeipa_user("alice")
        resp = self.client.post(reverse("membership-request-rescind", args=[req.pk]), follow=False)
        self.assertEqual(resp.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.status, MembershipRequest.Status.rescinded)
        self.assertEqual(req.decided_by_username, "alice")
        self.assertIsNotNone(req.decided_at)

        self.assertTrue(
            MembershipLog.objects.filter(
                membership_request=req,
                actor_username="alice",
                action=MembershipLog.Action.rescinded,
            ).exists()
        )
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="alice",
                action__type="request_rescinded",
            ).exists()
        )
