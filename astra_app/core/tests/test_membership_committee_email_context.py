from __future__ import annotations

from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from core.backends import FreeIPAUser
from core.membership_notifications import send_membership_notification
from core.membership_request_workflow import record_membership_request_created
from core.models import MembershipRequest, MembershipType


class MembershipCommitteeEmailContextTests(TestCase):
    def test_request_submitted_email_includes_committee_context_and_reply_to(self) -> None:
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
        alice = FreeIPAUser(
            "alice",
            {"uid": ["alice"], "mail": ["alice@example.com"], "memberof_group": []},
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=alice),
            patch("core.membership_request_workflow.post_office.mail.send") as send_mail,
        ):
            record_membership_request_created(
                membership_request=req,
                actor_username="reviewer",
                send_submitted_email=True,
            )

        send_mail.assert_called_once()
        _args, kwargs = send_mail.call_args
        context = kwargs.get("context") or {}
        self.assertEqual(
            context.get("membership_committee_email"),
            settings.MEMBERSHIP_COMMITTEE_EMAIL,
        )
        self.assertEqual(kwargs.get("headers"), {"Reply-To": settings.MEMBERSHIP_COMMITTEE_EMAIL})

    def test_membership_notifications_include_committee_context_and_reply_to(self) -> None:
        membership_type, _ = MembershipType.objects.update_or_create(
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

        with patch("core.membership_notifications.queue_templated_email") as queue_mail:
            send_membership_notification(
                recipient_email="alice@example.com",
                username="alice",
                membership_type=membership_type,
                template_name=settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
                expires_at=None,
                force=True,
                user_context={
                    "username": "alice",
                    "first_name": "",
                    "last_name": "",
                    "full_name": "",
                    "email": "alice@example.com",
                },
            )

        queue_mail.assert_called_once()
        _args, kwargs = queue_mail.call_args
        context = kwargs.get("context") or {}
        self.assertEqual(
            context.get("membership_committee_email"),
            settings.MEMBERSHIP_COMMITTEE_EMAIL,
        )
        self.assertEqual(kwargs.get("reply_to"), [settings.MEMBERSHIP_COMMITTEE_EMAIL])
