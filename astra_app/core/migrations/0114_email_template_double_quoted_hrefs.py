import re

from django.db import migrations

# Values containing a double quote (e.g. `{% url "name" %}`) are left alone: swapping quotes would end the attribute early.
SINGLE_QUOTED_HREF = re.compile(r"""\bhref='([^'"]*)'""")


def double_quote_hrefs(apps, schema_editor) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")
    for template in EmailTemplate.objects.filter(html_content__contains="href='") | EmailTemplate.objects.filter(
        content__contains="href='"
    ):
        template.content = SINGLE_QUOTED_HREF.sub(r'href="\1"', template.content or "")
        template.html_content = SINGLE_QUOTED_HREF.sub(r'href="\1"', template.html_content or "")
        template.save(update_fields=["content", "html_content"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0113_renewal_approved_email_invoice_block"),
    ]

    # Not reversible in a meaningful way: the original quoting is not recorded, and both forms render identically.
    operations = [
        migrations.RunPython(double_quote_hrefs, migrations.RunPython.noop),
    ]
