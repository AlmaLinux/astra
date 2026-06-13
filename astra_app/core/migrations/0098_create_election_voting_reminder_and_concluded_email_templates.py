from __future__ import annotations

from typing import Any

from django.db import migrations

from core.migration_helpers.email_template_text import text_from_html


def create_election_voting_reminder_template(apps: Any, schema_editor: Any) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    html_content = (
        "<p>Hello {{ full_name }},</p>\n"
        "<p>This is a friendly reminder that voting is currently open for the election: "
        "<strong>{{ election_name }}</strong>.</p>\n"
        "<p>{{ election_description }}</p>\n"
        "<p>Further information at: {{ election_url }}</p>\n"
        "\n"
        "<p>Voting will remain open until <strong>{{ election_end_datetime }}</strong> "
        "(subject to meeting the minimum quorum).</p>\n"
        "\n"
        "<hr>\n"
        "<h3>Your voting credential</h3>\n"
        "<p>Please keep this credential private. You will need it to access and submit your ballot.</p>\n"
        "<p><code>{{ credential_public_id }}</code></p>\n"
        "<p>Open the ballot using the link below. Your browser fills in the credential "
        "automatically; it is <strong>not</strong> sent to the server in the URL.</p>\n"
        "<p><a href=\"{{ vote_url_with_credential_fragment }}\">Open ballot</a></p>\n"
        "\n"
        "<hr>\n"
        "<h3>Important</h3>\n"
        "<ul>\n"
        "<li><strong>Do not share your credential.</strong></li>\n"
        "<li>If you submit multiple ballots, only your <strong>most recent submission</strong> "
        "before the election closes will be counted.</li>\n"
        "</ul>\n"
        "\n"
        "<p>If you have questions or encounter any issues, please contact "
        "<a href=\"mailto:{{ election_committee_email }}\">{{ election_committee_email }}</a>.</p>\n"
        "<p>Thank you for participating in the election!</p>\n"
        "<p><em>The AlmaLinux Team</em></p>"
    )

    EmailTemplate.objects.update_or_create(
        name="election-voting-reminder",
        defaults={
            "subject": "Reminder: voting is open for {{ election_name }}",
            "description": "A reminder email sent to eligible voters while an election is open.",
            "content": text_from_html(html_content),
            "html_content": html_content,
        },
    )


def create_election_concluded_template(apps: Any, schema_editor: Any) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    html_content = (
        "<p>Hello {{ full_name }},</p>\n"
        "<p>The election <strong>{{ election_name }}</strong> has now concluded and "
        "the results are available.</p>\n"
        "<p>Thank you for participating in the election.</p>\n"
        "\n"
        "<hr>\n"
        "<h3>Election results</h3>\n"
        "<p>The final results may be viewed at the link below:</p>\n"
        "<p><a href=\"{{ results_url }}\">View election results</a></p>\n"
        "\n"
        "<hr>\n"
        "<h3>Verify your ballot</h3>\n"
        "<p>As part of the election process, all anonymized ballots and ballot receipts "
        "have been published. If you saved your ballot receipt code and submission nonce, "
        "you may use them to confirm that your ballot was included in the final count.</p>\n"
        "<p><a href=\"{{ verify_url }}\">Verify your ballot</a></p>\n"
        "\n"
        "<hr>\n"
        "<h3>Privacy note</h3>\n"
        "<ul>\n"
        "<li>Ballots are published without voter identities.</li>\n"
        "<li>Your ballot receipt code does not display your vote choices.</li>\n"
        "</ul>\n"
        "\n"
        "<p>If you have questions regarding the election or its results, please contact "
        "<a href=\"mailto:{{ election_committee_email }}\">{{ election_committee_email }}</a>.</p>\n"
        "<p>Thank you for participating in the election!</p>\n"
        "<p><em>The AlmaLinux Team</em></p>"
    )

    EmailTemplate.objects.update_or_create(
        name="election-concluded",
        defaults={
            "subject": "{{ election_name }} — election results are available",
            "description": "An email sent to voters after an election has concluded, providing the results and verification information.",
            "content": text_from_html(html_content),
            "html_content": html_content,
        },
    )


def forward(apps: Any, schema_editor: Any) -> None:
    create_election_voting_reminder_template(apps, schema_editor)
    create_election_concluded_template(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0097_election_roll"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(
            forward,
            migrations.RunPython.noop,
        ),
    ]
