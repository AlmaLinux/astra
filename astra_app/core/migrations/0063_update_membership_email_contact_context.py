from __future__ import annotations

from django.db import migrations
from django.db.models import Q


def replace_committee_emails(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    membership_placeholder = "{{ membership_committee_email }}"
    membership_mailto = f"mailto:{membership_placeholder}"
    election_placeholder = "{{ election_committee_email }}"
    election_mailto = f"mailto:{election_placeholder}"

    templates = EmailTemplate.objects.filter(
        Q(html_content__icontains="membership@almalinux.org")
        | Q(content__icontains="membership@almalinux.org")
        | Q(html_content__icontains="elections@almalinux.org")
        | Q(content__icontains="elections@almalinux.org")
    )

    for template in templates:
        html_content = template.html_content or ""
        content = template.content or ""

        updated_html = html_content.replace("mailto:membership@almalinux.org", membership_mailto)
        updated_html = updated_html.replace("membership@almalinux.org", membership_placeholder)
        updated_html = updated_html.replace("mailto:elections@almalinux.org", election_mailto)
        updated_html = updated_html.replace("elections@almalinux.org", election_placeholder)

        updated_content = content.replace("membership@almalinux.org", membership_placeholder)
        updated_content = updated_content.replace("elections@almalinux.org", election_placeholder)

        if updated_html == html_content and updated_content == content:
            continue

        template.html_content = updated_html
        template.content = updated_content
        template.save(update_fields=["html_content", "content"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0062_account_invitation_token"),
    ]

    operations = [
        migrations.RunPython(replace_committee_emails, migrations.RunPython.noop),
    ]
