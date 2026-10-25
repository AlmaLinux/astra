from typing import Any

from django.db import migrations

_BATCH_SIZE = 500


def move_template_name_into_context(apps: Any, schema_editor: Any) -> None:
    """Record the template name in Email.context and clear Email.template.

    post_office re-renders template+context at delivery, discarding the stored
    rendering, so queued rows must not keep both. Rows without stored content
    really do depend on delivery-time rendering and are left alone.
    """

    Email = apps.get_model("post_office", "Email")

    rows = (
        Email.objects.filter(template__isnull=False)
        .exclude(message="", html_message="")
        .select_related("template")
    )
    batch = []
    for email in rows.iterator(chunk_size=_BATCH_SIZE):
        context = email.context if isinstance(email.context, dict) else {}
        email.context = {**context, "template_name": email.template.name}
        email.template = None
        batch.append(email)
        if len(batch) >= _BATCH_SIZE:
            Email.objects.bulk_update(batch, ["context", "template"])
            batch = []
    if batch:
        Email.objects.bulk_update(batch, ["context", "template"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0121_email_layout_template"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(move_template_name_into_context, migrations.RunPython.noop),
    ]
