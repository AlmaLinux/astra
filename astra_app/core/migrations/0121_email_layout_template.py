import re
from typing import Any

from django.core.files.storage import default_storage
from django.db import migrations

LAYOUT_NAME = "email-layout"
LOGO_STORAGE_KEY = "mail-images/Almalinux-logo-color-200px.png"

WRAP_PREFIX = f'{{% extends "{LAYOUT_NAME}" %}}{{% block content %}}\n'
WRAP_SUFFIX = "\n{% endblock %}"

# Templates that already extend something, or are complete documents, keep their own structure.
_SKIP_WRAP_RE = re.compile(r"{%-?\s*extends\b|<html", re.IGNORECASE)

# Content styles live in <style> so templates can stay plain HTML; the CSS inliner
# copies them onto each element at send time. Chrome styles stay inline.
_LAYOUT_HTML_TEMPLATE = """{% load post_office %}<!doctype html>
<html lang="en">
  <head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="x-apple-disable-message-reformatting" />
    <title>AlmaLinux OS Foundation</title>
    <style>
      .content { font-family: Arial, Helvetica, sans-serif; font-size: 16px; line-height: 1.6; color: #000518; }
      .content p { margin: 0 0 16px; }
      .content h1 { margin: 0 0 20px; font-family: Montserrat, Arial, Helvetica, sans-serif; font-weight: 300; font-size: 26px; line-height: 1.3; color: #0f4266; }
      .content h2 { margin: 24px 0 12px; font-family: Montserrat, Arial, Helvetica, sans-serif; font-weight: 400; font-size: 21px; line-height: 1.3; color: #0f4266; }
      .content h3 { margin: 24px 0 12px; font-family: Montserrat, Arial, Helvetica, sans-serif; font-weight: 600; font-size: 17px; line-height: 1.3; color: #0f4266; }
      .content a { color: #0069da; text-decoration: underline; }
      .content ul, .content ol { margin: 0 0 16px; padding-left: 24px; }
      .content li { margin: 0 0 6px; }
      .content hr { border: 0; border-top: 1px solid #d9e2ec; margin: 24px 0; }
      .content code { font-family: Consolas, Menlo, monospace; font-size: 14px; background-color: #f2f5f8; padding: 2px 4px; border-radius: 3px; }
      .content blockquote { margin: 0 0 16px; padding: 0 0 0 16px; border-left: 4px solid #d9e2ec; color: #264154; }
      .content .button { display: inline-block; padding: 14px 32px; font-size: 17px; font-weight: bold; color: #ffffff; background-color: #0069da; text-decoration: none; border-radius: 6px; }
      .footer a { color: #24c2ff; text-decoration: underline; }
      /* Below 1200px, 50% would drop under 600px: hold 600px there, shrinking to full width on phones. */
      @media (max-width: 1200px) {
        .container { width: 100% !important; max-width: 600px !important; }
      }
      @media (max-width: 620px) {
        .header, .content, .footer { padding-left: 20px !important; padding-right: 20px !important; }
      }
    </style>
  </head>
  <body style="margin: 0; padding: 0; background-color: #f2f5f8">
    <div style="display: none; max-height: 0; overflow: hidden; mso-hide: all">{% block preheader %}{% endblock %}</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f2f5f8">
      <tr>
        <td align="center" style="padding: 24px 12px">
          <table class="container" role="presentation" width="50%" cellpadding="0" cellspacing="0" border="0" style="width: 50%; background-color: #ffffff; border-radius: 8px; overflow: hidden">
            <tr>
              <td class="header" align="left" style="background-color: #ffffff; padding: 28px 32px 20px; border-bottom: 4px solid #0f4266">
                <img src="{% inline_image '__LOGO_URL__' %}" width="200" alt="AlmaLinux" style="display: block; width: 200px; max-width: 100%; height: auto; border: 0; font-family: Montserrat, 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 28px; font-weight: 700; line-height: 54px; color: #00245e" />
              </td>
            </tr>
            <tr>
              <td class="content" style="padding: 32px 32px 16px">
{% block content %}{% endblock %}
              </td>
            </tr>
            <tr>
              <td class="footer" style="background-color: #082336; padding: 20px 32px; font-family: Arial, Helvetica, sans-serif; font-size: 13px; line-height: 1.5; color: #c4e1ff">
                {% block footer %}{% if username %}You're receiving this because you have an AlmaLinux account. You can manage it at <a href="{{ public_base_url }}">AlmaLinux Account Services</a>.{% else %}Sent by <a href="{{ public_base_url }}">AlmaLinux Account Services</a> for the AlmaLinux OS Foundation. If you weren't expecting this email, you can safely ignore it.{% endif %}<br />
                <a href="https://almalinux.org/">almalinux.org</a>{% endblock %}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def create_email_layout_template(apps: Any, schema_editor: Any) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    # Same URL form the mail images page tells staff to paste, so both previews
    # (plain URL) and sending (inline attachment) can resolve it.
    logo_url = default_storage.url(LOGO_STORAGE_KEY)

    EmailTemplate.objects.update_or_create(
        name=LAYOUT_NAME,
        defaults={
            "subject": "",
            "description": (
                "Shared layout for all emails. Templates start with "
                f'{{% extends "{LAYOUT_NAME}" %}} and put their content in {{% block content %}}; '
                "they may also fill {% block preheader %} (inbox preview text) and {% block footer %}. "
                "The default footer points account holders (emails with a username) to their account, "
                "and tells everyone else they can ignore unexpected emails."
            ),
            "content": "",
            "html_content": _LAYOUT_HTML_TEMPLATE.replace("__LOGO_URL__", logo_url),
        },
    )


def wrap_email_templates_in_layout(apps: Any, schema_editor: Any) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    for template in EmailTemplate.objects.exclude(name=LAYOUT_NAME).exclude(html_content=""):
        if _SKIP_WRAP_RE.search(template.html_content):
            continue
        template.html_content = f"{WRAP_PREFIX}{template.html_content}{WRAP_SUFFIX}"
        template.save(update_fields=["html_content"])


def unwrap_email_templates_from_layout(apps: Any, schema_editor: Any) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    for template in EmailTemplate.objects.filter(html_content__startswith=WRAP_PREFIX):
        if template.html_content.endswith(WRAP_SUFFIX):
            template.html_content = template.html_content.removeprefix(WRAP_PREFIX).removesuffix(WRAP_SUFFIX)
            template.save(update_fields=["html_content"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0120_mattermostwebhookendpoint_condition"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        # Reverse keeps the layout row: deleting through historical models during a
        # backwards migration trips Django's related-object collection.
        migrations.RunPython(create_email_layout_template, migrations.RunPython.noop),
        migrations.RunPython(wrap_email_templates_in_layout, unwrap_email_templates_from_layout),
    ]
