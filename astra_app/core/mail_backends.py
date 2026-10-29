"""Email backends used by django-post-office (see POST_OFFICE['BACKENDS'])."""

from collections.abc import Sequence
from email.message import Message, MIMEPart
from typing import override

import django_ses
from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend
from django_ses.utils import filter_blacklisted_recipients


def nest_inline_images(message: EmailMessage) -> None:
    """Move inline (Content-ID) images into a multipart/related part with the HTML.

    post_office attaches them flat in multipart/mixed, next to the body. Clients
    such as Thunderbird then show them as attachments and leave `cid:` images
    in the HTML broken.
    """

    images = [part for part in message.attachments if isinstance(part, Message) and part["Content-ID"]]
    if not images or not isinstance(message, EmailMultiAlternatives):
        return

    html_alternatives = [alt for alt in message.alternatives if alt.mimetype == "text/html"]
    body = MIMEPart()
    if html_alternatives:
        body.set_content(message.body)
        body.add_alternative(html_alternatives[0].content, subtype="html")
        html_part = body.get_body(("html",))
    elif message.content_subtype == "html":
        body.set_content(message.body, subtype="html")
        html_part = body
    else:
        return

    for image in images:
        html_part.add_related(
            image.get_payload(decode=True),
            maintype=image.get_content_maintype(),
            subtype=image.get_content_subtype(),
            cid=image["Content-ID"],
            disposition="inline",
            filename=image.get_filename(),
        )

    # Django adds attachments under multipart/mixed, so the rebuilt body goes first.
    message.body = ""
    message.alternatives = []
    message.attachments = [body, *(part for part in message.attachments if not any(part is image for image in images))]


class RelatedInlineImagesMixin(BaseEmailBackend):
    @override
    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        for message in email_messages:
            nest_inline_images(message)
        return super().send_messages(email_messages)


class SMTPEmailBackend(RelatedInlineImagesMixin, SMTPBackend):
    pass


class BlacklistedRecipientsError(Exception):
    pass


class SESEmailBackend(RelatedInlineImagesMixin, django_ses.SESBackend):
    @override
    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        # django-ses silently skips a message whose recipients are all
        # blacklisted, so post_office would log it as sent. Raising makes
        # post_office record (and retry) it as failed instead.
        if settings.AWS_SES_USE_BLACKLIST:
            for message in email_messages:
                recipients = message.recipients()
                if recipients and not filter_blacklisted_recipients(recipients):
                    raise BlacklistedRecipientsError(f"All recipients are blacklisted: {', '.join(recipients)}")
        return super().send_messages(email_messages)
