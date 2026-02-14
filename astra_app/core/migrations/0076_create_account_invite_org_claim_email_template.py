from __future__ import annotations

from django.db import migrations

from core.migration_helpers.email_template_text import text_from_html


def add_account_invite_org_claim_template(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    html_content = (
        "<p>Hello{% if full_name %} {{ full_name }}{% endif %},</p>\n"
        "<p>Action required: AlmaLinux OS Foundation membership is moving to AlmaLinux Accounts.</p>\n"
        "<p>You must complete this to vote in upcoming elections and receive official communications.</p>\n"
        "<p>Steps:</p>\n"
        "<p>1) Create an AlmaLinux account: <a href=\"{{ register_url }}\">{{ register_url }}</a></p>\n"
        "<p>If you already have an account, sign in: <a href=\"{{ login_url }}\">{{ login_url }}</a></p>\n"
        "<p>2) Claim the organization {{ organization_name }}: <a href=\"{{ claim_url }}\">{{ claim_url }}</a></p>\n"
        "<p>After you claim it, {{ organization_name }} will be linked to your account for elections, voting, and official notices.</p>\n"
        "<p>If you have questions or need help, reply to this email and we will assist.</p>\n"
        "<p><em>The AlmaLinux Team</em></p>\n"
    )

    EmailTemplate.objects.update_or_create(
        name="account-invite-org-claim",
        defaults={
            "description": "Invite someone to create an AlmaLinux account and claim an organization",
            "subject": "Action required: create your AlmaLinux account and claim {{ organization_name }}",
            "html_content": html_content,
            "content": text_from_html(html_content),
        },
    )


def noop_reverse(apps, schema_editor) -> None:
    # Keep templates on rollback to avoid losing admin edits.
    return


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0075_organization_claim_fields"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(
            add_account_invite_org_claim_template,
            reverse_code=noop_reverse,
        ),
    ]
