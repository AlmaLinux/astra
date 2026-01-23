import datetime

from django.test import TestCase
from django.utils import timezone
from post_office.models import Email, EmailTemplate

from core.elections_services import (
    close_election,
    issue_voting_credential,
    send_vote_receipt_email,
    send_voting_credential_email,
    submit_ballot,
)
from core.models import Election


class ElectionPrivacyTest(TestCase):
    def setUp(self):
        self.election = Election.objects.create(
            name="Privacy Test Election",
            start_datetime=timezone.now() - datetime.timedelta(hours=1),
            end_datetime=timezone.now() + datetime.timedelta(hours=1),
            status=Election.Status.open,
            number_of_seats=1,
        )
        self.template = EmailTemplate.objects.create(
            name="test_template",
            subject="Test Subject",
            content="Test Content",
            html_content="<p>Test Content</p>",
        )
        # Mock settings to use this template
        from django.conf import settings
        self.original_cred_template = getattr(settings, "ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME", "credential_template")
        self.original_receipt_template = getattr(settings, "ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME", "receipt_template")
        settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME = "test_template"
        settings.ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME = "test_template"

    def tearDown(self):
        from django.conf import settings
        settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME = self.original_cred_template
        settings.ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME = self.original_receipt_template

    def test_emails_leak_sensitive_info(self):
        # 1. Issue Credential
        username = "alice"
        email_addr = "alice@example.com"
        cred = issue_voting_credential(election=self.election, freeipa_username=username, weight=1)
        
        send_voting_credential_email(
            request=None,
            election=self.election,
            username=username,
            email=email_addr,
            credential_public_id=cred.public_id,
        )

        # Verify email exists and contains credential ID
        emails = Email.objects.filter(to=email_addr)
        self.assertTrue(emails.exists())
        cred_email = emails.last()
        self.assertIn(cred.public_id, str(cred_email.context))

        # 2. Submit Vote
        from core.models import Candidate
        candidate = Candidate.objects.create(election=self.election, freeipa_username="bob")
        receipt = submit_ballot(
            election=self.election,
            credential_public_id=cred.public_id,
            ranking=[candidate.id],
        )

        send_vote_receipt_email(
            request=None,
            election=self.election,
            username=username,
            email=email_addr,
            receipt=receipt,
        )

        # Verify receipt email exists and contains ballot hash
        emails = Email.objects.filter(to=email_addr)
        self.assertEqual(emails.count(), 2)
        receipt_email = emails.last()
        self.assertIn(receipt.ballot.ballot_hash, str(receipt_email.context))

        # 3. Close Election
        close_election(election=self.election)

        # 4. Verify Anonymization of Credentials (this is already implemented)
        cred.refresh_from_db()
        self.assertIsNone(cred.freeipa_username)

        # 5. Verify Emails are deleted (The fix)
        emails_after_close = Email.objects.filter(to=email_addr)
        self.assertEqual(emails_after_close.count(), 0)
