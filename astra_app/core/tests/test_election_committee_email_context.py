from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from core.elections_services import send_vote_receipt_email, send_voting_credential_email
from core.models import Election


class ElectionCommitteeEmailContextTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        start_utc = timezone.make_aware(datetime.datetime(2026, 1, 2, 12, 0, 0), timezone=timezone.UTC)
        end_utc = timezone.make_aware(datetime.datetime(2026, 1, 2, 14, 0, 0), timezone=timezone.UTC)
        self.election = Election.objects.create(
            name="Committee context election",
            description="",
            start_datetime=start_utc,
            end_datetime=end_utc,
            number_of_seats=1,
            status=Election.Status.open,
        )

    def test_voting_credential_email_includes_committee_context_and_reply_to(self) -> None:
        with patch("core.elections_services.post_office.mail.send") as send_mail:
            send_voting_credential_email(
                request=None,
                election=self.election,
                username="alice",
                email="alice@example.com",
                credential_public_id="CRED-1",
                subject_template="{{ election_committee_email }}",
                html_template="",
                text_template="",
            )

        send_mail.assert_called_once()
        _args, kwargs = send_mail.call_args
        self.assertEqual(kwargs.get("subject"), settings.ELECTION_COMMITTEE_EMAIL)
        self.assertEqual(kwargs.get("headers"), {"Reply-To": settings.ELECTION_COMMITTEE_EMAIL})

    def test_vote_receipt_email_includes_committee_context_and_reply_to(self) -> None:
        receipt = SimpleNamespace(
            ballot=SimpleNamespace(
                ballot_hash="hash",
                weight=1,
                previous_chain_hash="prev",
                chain_hash="cur",
            ),
            nonce="nonce",
        )

        with patch("core.elections_services.queue_templated_email") as queue_mail:
            send_vote_receipt_email(
                request=None,
                election=self.election,
                username="alice",
                email="alice@example.com",
                receipt=receipt,
            )

        queue_mail.assert_called_once()
        _args, kwargs = queue_mail.call_args
        context = kwargs.get("context") or {}
        self.assertEqual(context.get("election_committee_email"), settings.ELECTION_COMMITTEE_EMAIL)
        self.assertEqual(kwargs.get("reply_to"), [settings.ELECTION_COMMITTEE_EMAIL])
