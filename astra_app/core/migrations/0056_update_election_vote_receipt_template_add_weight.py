from __future__ import annotations

from django.db import migrations


def update_election_vote_receipt_template_add_weight(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    EmailTemplate.objects.update_or_create(
        name="election-vote-receipt",
        defaults={
            "subject": "Vote receipt for {{ election_name }}",
            "content": (
                "Hello {{ username }},\n"
                "\n"
                "Your vote for {{ election_name }} has been successfully recorded.\n"
                "\n"
                "Election closes: {{ election_end_datetime }}\n"
                "\n---\n\n"
                "Your ballot receipt:\n\n"
                "Ballot receipt code:\n"
                "{{ ballot_hash }}\n"
                "\n"
                "Submission nonce:\n"
                "{{ nonce }}\n"
                "\n"
                "Vote weight:\n"
                "{{ weight }}\n"
                "\n"
                "Please save the receipt code and the nonce if you want to verify your vote later. Together, they allow you to confirm that the system recorded your ballot correctly.\n"
                "\n---\n\n"
                "Verify your ballot:\n\n"
                "You can verify that your ballot was recorded and included in the election ledger here:\n"
                "{{ verify_url }}\n"
                "\n"
                "This verification confirms that a ballot corresponding to your receipt exists in the system. It does not display your vote choices.\n"
                "\n---\n\n"
                "Ballot integrity ledger (advanced):\n\n"
                "To make ballot storage tamper-evident, ballots are recorded in an append-only cryptographic ledger.\n"
                "* Previous ledger hash: {{ previous_chain_hash }}\n"
                "* Current ledger hash:  {{ chain_hash }}\n"
                "\n"
                "These values allow independent auditors to verify that ballots were not altered or removed after submission.\n"
                "\n---\n\n"
                "Important information:\n"
                "* Only your **most recent ballot** submitted before the election closes will be counted.\n"
                "* If you vote again, earlier ballots are automatically superseded.\n"
                "* After the election is closed and tallied, all anonymized ballots and receipts will be published.\n"
                "* You may then look for your receipt code in the published list to confirm that your ballot was included in the final count.\n"
                "\n---\n\n"
                "Privacy note:\n"
                "* Your receipt does not display your vote choices.\n"
                "* Ballots are published without voter identities.\n"
                "* The system provides transparency and individual verification, but it does not prevent voters from voluntarily sharing their receipt.\n"
                "\n---\n\n"
                "Thank you for participating in the election!\n"
                "\n"
                "-- The AlmaLinux Team\n"
            ),
            "html_content": (
                "<p>Hello {{ username }},</p>\n"
                "<p>Your vote for {{ election_name }} has been successfully recorded.</p>\n"
                "<p><strong>The election closes: {{ election_end_datetime }}</strong></p>\n"
                "<hr>\n"
                "<h3>Your ballot receipt</h3>\n"
                "<p><strong>Ballot receipt code:</strong><br/><code>{{ ballot_hash }}</code></p>\n"
                "<p><strong>Submission nonce:</strong><br/><code>{{ nonce }}</code></p>\n"
                "<p><strong>Vote weight:</strong><br/><code>{{ weight }}</code></p>\n"
                "<p>Please save the receipt code and the nonce if you want to verify your vote later. Together, they allow you to confirm that the system recorded your ballot correctly.</p>\n"
                "<hr>\n"
                "<h3>Verify your ballot</h3>\n"
                "<p>You can verify that your ballot was recorded and included in the election ledger here:</p>\n"
                "<p><a href=\"{{ verify_url }}\">{{ verify_url }}</a></p>\n"
                "<p>This verification confirms that a ballot corresponding to your receipt exists in the system. It does <strong>not</strong> display your vote choices.</p>\n"
                "<hr>\n"
                "<h3>Ballot integrity ledger (advanced):</h3>\n"
                "<p>To make ballot storage tamper-evident, ballots are recorded in an append-only cryptographic ledger.</p>\n"
                "<ul>\n"
                "<li>Previous ledger hash: <code>{{ previous_chain_hash }}</code></li>\n"
                "<li>Current ledger hash:  <code>{{ chain_hash }}</code></li>\n"
                "</ul>\n"
                "<p>These values allow independent auditors to verify that ballots were not altered or removed after submission.</p>\n"
                "<hr>\n"
                "<h3>Important information</h3>\n"
                "<ul>\n"
                "<li>Only your <strong>most recent ballot</strong> submitted before the election closes will be counted.</li>\n"
                "<li>If you vote again, earlier ballots are automatically superseded.</li>\n"
                "<li>After the election is closed and tallied, <strong>all anonymized ballots and receipts will be published</strong>.</li>\n"
                "<li>You may then look for your receipt code in the published list to confirm that your ballot was included in the final count.</li>\n"
                "</ul>\n"
                "<h3>Privacy note</h3>\n"
                "<ul>\n"
                "<li>Your receipt does not display your vote choices.</li>\n"
                "<li>Ballots are published without voter identities.</li>\n"
                "<li>The system provides transparency and individual verification, but it does not prevent voters from voluntarily sharing their receipt.</li>\n"
                "</ul>\n"
                "<p>Thank you for participating in the election!</p>\n"
                "<p><em>The AlmaLinux Team</em></p>"
            ),
        },
    )


def noop_reverse(*_args, **_kwargs) -> None:
    # Keep templates on rollback to avoid losing admin edits.
    return


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0055_add_view_user_directory_permission"),
    ]

    operations = [
        migrations.RunPython(
            update_election_vote_receipt_template_add_weight,
            reverse_code=noop_reverse,
        ),
    ]
