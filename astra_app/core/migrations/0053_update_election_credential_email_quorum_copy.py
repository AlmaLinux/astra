from __future__ import annotations

from django.db import migrations

from core.migration_helpers.email_template_text import text_from_html


def update_election_credential_copy(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    template = EmailTemplate.objects.filter(name="election-voting-credential").first()
    if template is None:
        return

    html = str(template.html_content or "")
    updated = html.replace("(subject to meeting the minimum quota)", "(subject to meeting the minimum quorum)")
    if updated == html:
        return

    template.html_content = updated
    template.content = text_from_html(updated)
    template.save(update_fields=["html_content", "content"])


def noop_reverse(apps, schema_editor) -> None:
    return


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0052_membershiprequest_one_open_org_request"),
    ]

    operations = [
        migrations.RunPython(update_election_credential_copy, reverse_code=noop_reverse),
    ]
