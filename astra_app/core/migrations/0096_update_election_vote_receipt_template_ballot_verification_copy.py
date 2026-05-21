from __future__ import annotations

from typing import Any

from django.db import migrations

from core.migration_helpers.email_template_text import text_from_html


def update_election_vote_receipt_template_ballot_verification_copy(apps: Any, schema_editor: Any) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    html_content = (
        "<p>Hello {{ username }},</p>\n"
        "<p>Your vote for {{ election_name }} has been successfully recorded.</p>\n"
        "<p><strong>The election closes: {{ election_end_datetime }}</strong></p>\n"
        "<hr>\n"
        "<h3>Your ballot receipt</h3>\n"
        "<p><strong>Ballot receipt code:</strong><br/><code>{{ ballot_hash }}</code></p>\n"
        "<p><strong>Submission nonce:</strong><br/><code>{{ nonce }}</code></p>\n"
        "<p><strong>Vote weight:</strong><br/><code>{{ weight }}</code></p>\n"
        "<p>Please save the ballot receipt code and the submission nonce if you want to verify your vote later. Together, they let you confirm that the system recorded your ballot correctly.</p>\n"
        "<hr>\n"
        "<h3>Verify your ballot</h3>\n"
        "<p>You can verify that your ballot was recorded and included in the election ledger here:</p>\n"
        "<p><a href=\"{{ verify_url }}\">{{ verify_url }}</a></p>\n"
        "<p>This verification confirms that a ballot with your ballot receipt code is recorded in the system. It does <strong>not</strong> display your vote choices.</p>\n"
        "<hr>\n"
        "<h3>Ballot integrity ledger (advanced):</h3>\n"
        "<p>To make ballot storage tamper-evident, ballots are recorded in an append-only cryptographic ledger.</p>\n"
        "<ul>\n"
        "<li>Previous ledger hash: <code>{{ previous_chain_hash }}</code></li>\n"
        "<li>Current ledger hash: <code>{{ chain_hash }}</code></li>\n"
        "</ul>\n"
        "<p>These values allow independent auditors to verify that ballots were not altered or removed after submission.</p>\n"
        "<hr>\n"
        "<h3>Important information</h3>\n"
        "<ul>\n"
        "<li>Only your <strong>most recent ballot</strong> submitted before the election closes will be counted.</li>\n"
        "<li>If you vote again, earlier ballots are automatically superseded.</li>\n"
        "<li>After the election is closed and tallied, <strong>all anonymized ballots and receipts will be published</strong>.</li>\n"
        "<li>You may then look for your ballot receipt code in the published list to confirm that your ballot was included in the final count.</li>\n"
        "</ul>\n"
        "<h3>Privacy note</h3>\n"
        "<ul>\n"
        "<li>Your ballot receipt code does not display your vote choices.</li>\n"
        "<li>Ballots are published without voter identities.</li>\n"
        "<li>The system provides transparency and individual verification, but it does not prevent voters from voluntarily sharing their ballot receipt code.</li>\n"
        "</ul>\n"
        "<p>Thank you for participating in the election!</p>\n"
        "<p><em>The AlmaLinux Team</em></p>"
    )

    EmailTemplate.objects.update_or_create(
        name="election-vote-receipt",
        defaults={
            "subject": "Vote receipt for {{ election_name }}",
            "content": text_from_html(html_content),
            "html_content": html_content,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0095_create_membership_renewal_approved_email_template"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(
            update_election_vote_receipt_template_ballot_verification_copy,
            migrations.RunPython.noop,
        ),
    ]