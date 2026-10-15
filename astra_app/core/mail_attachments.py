"""Files attached to Send Mail emails.

The Send Mail page is a plain form that re-renders after every action, and browsers
can't resubmit a picked file. So uploads are stored right away and the page carries a
signed reference to each one; signing also lets server code pre-attach files it
already stored (e.g. an invoice) without letting users attach arbitrary storage keys.
"""

import datetime
import mimetypes
import secrets
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from django.conf import settings
from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone
from post_office.models import Attachment
from post_office.utils import create_attachments

from core.tokens import make_mail_attachment_token, read_mail_attachment_token

MAIL_ATTACHMENTS_UPLOAD_DIR = "send-mail-attachments"
# Every recipient's copy carries the files; stay well under SES's 40 MB message limit
# once base64 encoding is accounted for.
MAIL_ATTACHMENTS_MAX_TOTAL_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class MailAttachment:
    key: str
    name: str
    mimetype: str
    size: int

    def to_token(self) -> str:
        return make_mail_attachment_token(asdict(self))


def mail_attachment_from_storage(*, key: str, name: str, mimetype: str) -> MailAttachment:
    return MailAttachment(key=key, name=name, mimetype=mimetype, size=default_storage.size(key))


def _upload_name(upload: UploadedFile) -> str:
    return PurePosixPath(str(upload.name or "attachment")).name


def store_uploaded_mail_attachment(upload: UploadedFile) -> MailAttachment:
    name = _upload_name(upload)
    key = default_storage.save(f"{MAIL_ATTACHMENTS_UPLOAD_DIR}/{secrets.token_urlsafe(16)}/{name}", upload)
    mimetype = upload.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    return MailAttachment(key=key, name=name, mimetype=mimetype, size=upload.size)


def mail_attachments_from_tokens(tokens: Iterable[str]) -> list[MailAttachment]:
    attachments: list[MailAttachment] = []
    for token in tokens:
        try:
            attachments.append(MailAttachment(**read_mail_attachment_token(token)))
        except (signing.BadSignature, TypeError) as exc:
            raise ValueError("An attachment has expired or is invalid. Please attach the file again.") from exc
    return attachments


def validate_mail_attachments(attachments: list[MailAttachment]) -> None:
    if sum(attachment.size for attachment in attachments) > MAIL_ATTACHMENTS_MAX_TOTAL_BYTES:
        limit_mb = MAIL_ATTACHMENTS_MAX_TOTAL_BYTES // (1024 * 1024)
        raise ValueError(f"Attachments are too large: the total limit is {limit_mb} MB.")
    # post_office keys attachments by filename, so a duplicate would silently replace another.
    names = [attachment.name for attachment in attachments]
    if len(set(names)) != len(names):
        raise ValueError("Two attachments have the same file name. Rename one of them.")


def store_uploaded_mail_attachments(
    uploads: list[UploadedFile],
    *,
    attached: list[MailAttachment],
) -> list[MailAttachment]:
    """Store new uploads, validating them together with the files already attached.

    Validation runs first, so rejected uploads are never stored.
    """

    validate_mail_attachments(
        [*attached, *(MailAttachment(key="", name=_upload_name(upload), mimetype="", size=upload.size) for upload in uploads)]
    )
    return [store_uploaded_mail_attachment(upload) for upload in uploads]


def create_post_office_attachments(attachments: Iterable[MailAttachment]) -> list[Attachment]:
    """Copy the files into post_office once, so every email in a run shares them."""

    files: dict[str, dict[str, object]] = {}
    for attachment in attachments:
        with default_storage.open(attachment.key, "rb") as stored:
            files[attachment.name] = {"file": ContentFile(stored.read()), "mimetype": attachment.mimetype}
    return create_attachments(files)


def discard_uploaded_mail_attachments(attachments: Iterable[MailAttachment]) -> None:
    # Only temporary uploads: pre-attached files (e.g. invoices) are owned elsewhere.
    for attachment in attachments:
        if attachment.key.startswith(f"{MAIL_ATTACHMENTS_UPLOAD_DIR}/"):
            default_storage.delete(attachment.key)


def delete_stale_mail_attachment_uploads(*, dry_run: bool = False) -> int:
    """Delete uploads whose references have expired: they can never be sent.

    Returns how many were (or, with *dry_run*, would be) deleted.
    """

    cutoff = timezone.now() - datetime.timedelta(seconds=settings.MAIL_ATTACHMENT_TOKEN_TTL_SECONDS)
    deleted = 0
    try:
        upload_dirs, _files = default_storage.listdir(MAIL_ATTACHMENTS_UPLOAD_DIR)
    except FileNotFoundError:
        return 0
    for upload_dir in upload_dirs:
        _subdirs, names = default_storage.listdir(f"{MAIL_ATTACHMENTS_UPLOAD_DIR}/{upload_dir}")
        for name in names:
            key = f"{MAIL_ATTACHMENTS_UPLOAD_DIR}/{upload_dir}/{name}"
            if default_storage.get_modified_time(key) < cutoff:
                if not dry_run:
                    default_storage.delete(key)
                deleted += 1
    return deleted
