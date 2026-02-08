
import json
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType, Note
from core.permissions import ASTRA_ADD_SEND_MAIL


class SendMailMembershipContactedNoteTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def setUp(self) -> None:
        super().setUp()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

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

    def test_send_records_contacted_action_note_when_membership_request_id_is_provided(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.views_send_mail.EmailMultiAlternatives", autospec=True) as email_cls,
        ):
            email_cls.return_value.send.return_value = 1
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "alice@example.com",
                    "cc": "",
                    "bcc": "",
                    "subject": "Hello",
                    "html_content": "<p>Hi</p>",
                    "text_content": "Hi",
                    "action": "send",
                    "extra_context_json": json.dumps({"membership_request_id": str(req.pk)}),
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        email_cls.assert_called()
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action__type="contacted",
                action__kind="custom",
            ).exists()
        )
