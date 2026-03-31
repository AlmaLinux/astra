from __future__ import annotations

from typing import Any

from django.db import migrations


def create_membership_renewal_approved_email_template(apps: Any, schema_editor: Any) -> None:
    EmailTemplate = apps.get_model("post_office", "EmailTemplate")

    EmailTemplate.objects.update_or_create(
        name="membership-renewal-approved",
        defaults={
            "description": "Approval for a membership renewal",
            "subject": "Membership Renewal Approved!",
            "content": (
                "Hi there, {{ full_name }}!\n\n"
                "We are happy to share that your membership renewal request has been approved! Thank you for your continued support of the AlmaLinux OS Foundation.\n\n"
                "If you ever have any questions about AlmaLinux, or want to find out more ways you can get involved don't hesitate to ask them.\n\n"
                "If you are looking to increase your involvement in the community, there are a few different ways to do so! You can start by taking a look at the [Contribute](https://almalinux.org/contribute/) page on the website. We have a [number of SIGs](https://wiki.almalinux.org/sigs/) that are actively working on all kinds of fun stuff, or you can help by answering questions on the [forums](https://forums.almalinux.org) or [reddit](https://www.reddit.com/r/AlmaLinux/).\n\n"
                "**Get connected**\n\n"
                "If you're not already, please join our chat server at [chat.almalinux.org](https://chat.almalinux.org/). If you want to sport your very own AlmaLinux gear, head over to [shop.almalinux.org](https://shop.almalinux.org) to pick up some goodies! We've got hoodies, shirts, mugs, phone cases and much more.\n\n"
                "Thanks again, and welcome to the AlmaLinux OS Foundation.\n\n"
                "-- The AlmaLinux Team\n"
            ),
            "html_content": (
                "<p>Hi there, {{ full_name }}!</p>\n"
                "<p>We are happy to share that your membership renewal request has been approved! Thank you for your continued support of the AlmaLinux OS Foundation.</p>\n"
                "<p>If you ever have any questions about AlmaLinux, or want to find out more ways you can get involved don't hesitate to ask them.</p>\n"
                '<p>If you are looking to increase your involvement in the community, there are a few different ways to do so! You can start by taking a look at the <a href="https://almalinux.org/contribute/">Contribute</a> page on the website. We have a <a href="https://wiki.almalinux.org/sigs/">number of SIGs</a> that are actively working on all kinds of fun stuff, or you can help by answering questions  on the <a href="https://forums.almalinux.org">forums</a> or <a href="https://www.reddit.com/r/AlmaLinux/">reddit</a>.</p>\n'
                "<p><strong>Get connected</strong></p>\n"
                '<p>If you\'re not already, please join our chat server at <a href="https://chat.almalinux.org/">chat.almalinux.org</a>. If you want to sport your very own AlmaLinux gear, head over to <a href="https://shop.almalinux.org">shop.almalinux.org</a> to pick up some goodies! We\'ve got hoodies, shirts, mugs, phone cases and much more.</p>\n'
                "<p>Thanks again, and welcome to the AlmaLinux OS Foundation.</p>\n"
                "<p><em>The AlmaLinux Team</em></p>"
            ),
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0094_update_membership_committee_pending_requests_email_template"),
        ("post_office", "0013_email_recipient_delivery_status_alter_log_status"),
    ]

    operations = [
        migrations.RunPython(
            create_membership_renewal_approved_email_template,
            migrations.RunPython.noop,
        ),
    ]
