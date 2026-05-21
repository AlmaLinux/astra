from importlib import import_module

from django.apps import apps
from django.conf import settings
from django.test import TestCase


class ElectionVoteReceiptTemplateCopyMigrationTests(TestCase):
    def test_migration_updates_vote_receipt_template_to_canonical_verification_terms(self) -> None:
        from post_office.models import EmailTemplate

        EmailTemplate.objects.update_or_create(
            name=settings.ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME,
            defaults={
                "subject": "Old subject",
                "content": "Receipt code {{ ballot_hash }} and nonce {{ nonce }}",
                "html_content": "<p>Receipt code {{ ballot_hash }}</p>",
            },
        )

        migration = import_module(
            "core.migrations.0096_update_election_vote_receipt_template_ballot_verification_copy"
        )
        migration.update_election_vote_receipt_template_ballot_verification_copy(apps, None)

        template = EmailTemplate.objects.get(name=settings.ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME)

        self.assertIn("Ballot receipt code:", template.content)
        self.assertIn("Submission nonce:", template.content)
        self.assertIn("Previous ledger hash:", template.content)
        self.assertIn("Current ledger hash:", template.content)
        self.assertIn(
            "Together, they let you confirm that the system recorded your ballot correctly.",
            template.content,
        )
        self.assertIn(
            "This verification confirms that a ballot with your ballot receipt code is recorded in the system.",
            template.content,
        )
        self.assertIn("It does **not** display your vote choices.", template.content)
        self.assertIn("Ballot receipt code:", template.html_content)
        self.assertIn("Submission nonce:", template.html_content)
        self.assertIn("Previous ledger hash:", template.html_content)
        self.assertIn("Current ledger hash:", template.html_content)
        self.assertIn(
            "Together, they let you confirm that the system recorded your ballot correctly.",
            template.html_content,
        )
        self.assertIn(
            "This verification confirms that a ballot with your ballot receipt code is recorded in the system.",
            template.html_content,
        )
        self.assertIn("It does <strong>not</strong> display your vote choices.", template.html_content)
        self.assertNotIn(
            "Together, they allow you to confirm that the system recorded your ballot correctly.",
            template.content,
        )
        self.assertNotIn(
            "a ballot corresponding to your ballot receipt code exists in the system",
            template.content,
        )